"""Bounded local wire contract shared by native Windows and Ubuntu sidecars."""
import json
import math
import re
import struct

MAX_JSON = 64 * 1024
MAX_IMAGE = 16 * 1024 * 1024
MAX_STAMP = 2147483647 * 1_000_000_000 + 999_999_999
COMMAND_AGE_NS = 250_000_000
FUTURE_TOLERANCE_NS = 50_000_000


class ProtocolError(ValueError):
    pass


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', value):
        raise ProtocolError('invalid robot_id')
    return value


def integer(value, name, minimum=0, maximum=MAX_STAMP):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProtocolError('invalid ' + name)
    return value


def number(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ProtocolError('nonfinite or invalid ' + name)
    return float(value)


def vector(value, length, name):
    if not isinstance(value, list) or len(value) != length:
        raise ProtocolError('invalid ' + name)
    return [number(v, name) for v in value]


def pose(value):
    if not isinstance(value, dict):
        raise ProtocolError('invalid pose')
    vector(value.get('position_m'), 3, 'position_m')
    q = vector(value.get('rotation_xyzw'), 4, 'rotation_xyzw')
    if abs(sum(v*v for v in q)-1) > .001:
        raise ProtocolError('rotation quaternion is not normalized')


def validate_state(state, image, robot_id):
    if not isinstance(state, dict) or state.get('v') not in (1,2) or type(state.get('v')) is not int or state.get('kind') != 'state':
        raise ProtocolError('unsupported state protocol')
    if state.get('robot_id') != identifier(robot_id):
        raise ProtocolError('state belongs to another robot')
    if 'map' in state:
        scene=state['map']
        if not isinstance(scene,dict) or not isinstance(scene.get('id'),str) or not 1<=len(scene['id'])<=128 or scene.get('kind') not in ('rm_flat_map','rm_mesh_map') or not isinstance(scene.get('source_md5'),str) or not re.fullmatch('[0-9a-fA-F]{32}',scene['source_md5']):raise ProtocolError('invalid map identity')
    session = state.get('session')
    if not isinstance(session, str) or not 1 <= len(session) <= 128:
        raise ProtocolError('invalid session')
    for key in ('epoch', 'tick', 'sim_time_ns'):
        integer(state.get(key), key)
    pose(state.get('pose'))
    camera = state.get('camera')
    if not isinstance(camera, dict):
        raise ProtocolError('missing camera')
    width = integer(camera.get('width'), 'camera width', 1, 4096)
    height = integer(camera.get('height'), 'camera height', 1, 4096)
    fov = number(camera.get('hfov_rad'), 'camera HFOV')
    if not 0 < fov < math.pi or width*height*3 > MAX_IMAGE:
        raise ProtocolError('camera exceeds supported dimensions/FOV')
    if camera.get('frame_id') != robot_id + '/aim_camera_optical_frame':
        raise ProtocolError('wrong optical frame')
    stamp = integer(camera.get('stamp_ns'), 'camera stamp')
    if stamp > state['sim_time_ns'] + FUTURE_TOLERANCE_NS:
        raise ProtocolError('camera stamp is ahead of simulation')
    pose(camera.get('pose_in_base'))
    camera_bytes=len(image) if state['v']==1 else integer(state.get('camera_bytes'),'camera bytes',0,MAX_IMAGE)
    if camera_bytes and camera_bytes != width*height*3:
        raise ProtocolError('RGB8 payload does not match camera dimensions')
    if state['v']==2:
        validate_lidar(state.get('lidar'),image,camera_bytes,robot_id,state['sim_time_ns'])
        if any(p.get('id')==state['lidar']['id'] for p in state.get('imu',{}).get('profiles',[])):raise ProtocolError('duplicate sensor frames')
    elif 'lidar' in state:raise ProtocolError('LiDAR requires v2 binary layout')
    command = state.get('command')
    if not isinstance(command, dict) or not isinstance(command.get('status'), str) or len(command['status']) > 128:
        raise ProtocolError('invalid command acknowledgement')
    integer(command.get('accepted_seq'), 'accepted sequence', -1)
    number(command.get('age_s'), 'command age')
    if 'aim' in state:validate_aim_state(state['aim'],state['sim_time_ns'])
    if 'imu' in state:validate_imu(state['imu'],robot_id,state['sim_time_ns'])
    if 'training_evaluation' in state and not isinstance(state['training_evaluation'],dict):raise ProtocolError('invalid training evaluation')
    return state


def validate_aim_state(value,sim_time_ns):
    if not isinstance(value,dict):raise ProtocolError('invalid aim state')
    for key in ('enabled','mechanically_limited','trigger_active'):
        if type(value.get(key)) is not bool:raise ProtocolError('invalid aim flag')
    for key in ('status','last_rejection','yaw_joint','pitch_joint','actuator_model'):
        if not isinstance(value.get(key),str) or len(value[key])>256:raise ProtocolError('invalid aim text')
    integer(value.get('accepted_seq'),'aim acknowledgement',-1)
    integer(value.get('rejected_count'),'aim rejections')
    stamp=integer(value.get('stamp_ns'),'aim feedback stamp')
    if stamp>sim_time_ns+FUTURE_TOLERANCE_NS:raise ProtocolError('aim feedback ahead of simulation')
    for key in ('command_age_s','yaw_rad','pitch_rad','world_yaw_rad','world_pitch_rad','yaw_velocity_rad_s',
                'pitch_velocity_rad_s','pitch_min_rad','pitch_max_rad','yaw_rate_limit','pitch_rate_limit',
                'target_world_yaw','target_world_pitch'):number(value.get(key),key)
    shooter=value.get('shooter')
    if not isinstance(shooter,dict) or type(shooter.get('available')) is not bool:raise ProtocolError('invalid shooter state')
    allowed={'available','status','epoch','time','ammo','heat','shots','clock_source','max_heat','heat_per_shot','shot_interval','cooling_per_second','launch_cm_s'}
    if set(shooter)-allowed:raise ProtocolError('evaluation data must not enter own shooter state')
    if shooter['available']:
        for key in ('epoch','ammo','shots'):integer(shooter.get(key),key)
        for key in ('time','heat','max_heat','heat_per_shot','shot_interval','cooling_per_second','launch_cm_s'):
            if number(shooter.get(key),key)<0:raise ProtocolError('negative shooter value')
        if not isinstance(shooter.get('status'),str):raise ProtocolError('invalid training phase')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError('duplicate JSON key: ' + key)
        result[key] = value
    return result


class PacketReader:
    """TCP fragmentation is normal: retain partial header/JSON/image until complete."""
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk):
        self.buffer.extend(chunk)
        packets = []
        while len(self.buffer) >= 8:
            json_size, image_size = struct.unpack_from('<II', self.buffer)
            if not 2 <= json_size <= MAX_JSON or image_size > MAX_IMAGE:
                raise ProtocolError('packet length outside limits')
            total = 8 + json_size + image_size
            if len(self.buffer) < total:
                break
            try:
                state = json.loads(bytes(self.buffer[8:8+json_size]).decode('utf-8'), object_pairs_hook=unique_object)
            except (UnicodeError, ValueError, RecursionError) as error:
                raise ProtocolError('invalid state JSON') from error
            image = bytes(self.buffer[8+json_size:total])
            del self.buffer[:total]
            packets.append((state, image))
        if len(self.buffer) > 8 + MAX_JSON + MAX_IMAGE:
            raise ProtocolError('partial packet exceeds limits')
        return packets


def intrinsics(width, height, hfov_rad):
    focal = width/(2*math.tan(hfov_rad/2))
    return [focal, 0., width/2, 0., focal, height/2, 0., 0., 1.]


def command_packet(state, sequence, stamp_ns, frame_id, values, robot_id):
    """values = linear x/y/z, angular x/y/z; unsupported components never disappear silently."""
    integer(sequence, 'sequence', 1)
    integer(stamp_ns, 'command stamp')
    if frame_id != robot_id + '/base_link':
        raise ProtocolError('cmd_vel must use the robot base_link frame')
    if len(values) != 6:
        raise ProtocolError('invalid twist')
    values = [number(v, 'twist') for v in values]
    if any(abs(values[i]) > 1e-9 for i in (2, 3, 4)):
        raise ProtocolError('only planar vx/vy/wz are supported')
    age = state['sim_time_ns'] - stamp_ns
    if age > COMMAND_AGE_NS or age < -FUTURE_TOLERANCE_NS:
        raise ProtocolError('stale or future cmd_vel stamp')
    payload = dict(v=1, kind='command', session=state['session'], epoch=state['epoch'],
                   seq=sequence, stamp_ns=stamp_ns, frame_id=frame_id,
                   vx=values[0], vy=values[1], wz=values[5])
    raw = json.dumps(payload, allow_nan=False, separators=(',', ':')).encode('utf-8') + b'\n'
    if len(raw) > 4096:
        raise ProtocolError('command line exceeds limit')
    return raw


def motor_packet(state, sequence, steer, wheel, brake, robot_id):
    integer(sequence, 'sequence', 1)
    if not isinstance(brake, bool) or len(steer)!=4 or len(wheel)!=4:
        raise ProtocolError('invalid motor command shape')
    values=[integer(v,'motor current',-2000,2000) for v in list(steer)+list(wheel)]
    payload=dict(v=1,kind='motor_current',session=state['session'],epoch=state['epoch'],
                 seq=sequence,stamp_ns=state['sim_time_ns'],frame_id=identifier(robot_id)+'/base_link',brake=int(brake))
    payload.update({f'steer{i}':values[i] for i in range(4)})
    payload.update({f'wheel{i}':values[4+i] for i in range(4)})
    return json.dumps(payload,allow_nan=False,separators=(',',':')).encode('utf-8')+b'\n'


def hold_aim():
    return dict(yaw_type=0,pitch_type=0,yaw=0.,pitch=0.,yaw_rate=0.,pitch_rate=0.,
                yaw_min=-math.pi,yaw_max=math.pi,pitch_min=-math.pi/2,pitch_max=math.pi/2,fire=0,permit=0)


def aim_packet(state, sequence, stamp_ns, command):
    integer(sequence,'aim sequence',1);integer(stamp_ns,'aim stamp')
    age=state['sim_time_ns']-stamp_ns
    if age>200_000_000 or age < -FUTURE_TOLERANCE_NS:raise ProtocolError('stale or future aim stamp')
    if set(command)!=set(hold_aim()):raise ProtocolError('invalid aim fields')
    for axis,maximum in [('yaw',math.pi),('pitch',math.pi/2)]:
        mode=integer(command[axis+'_type'],'aim mode',0,2)
        for field in (axis,axis+'_min',axis+'_max'):
            if abs(number(command[field],field))>maximum:raise ProtocolError('aim angle/range exceeds limits')
        if abs(number(command[axis+'_rate'],'aim rate'))>50:raise ProtocolError('aim rate exceeds limit')
        if mode==2 and command[axis+'_max']-command[axis+'_min']<=1.e-5:raise ProtocolError('empty scan range')
    for flag in ('fire','permit'):integer(command[flag],flag,0,1)
    if command['fire'] and not command['permit']:raise ProtocolError('fire requires permission')
    payload=dict(v=1,kind='aim',session=state['session'],epoch=state['epoch'],seq=sequence,stamp_ns=stamp_ns,frame_id='rmsim_world')
    payload.update(command)
    return json.dumps(payload,allow_nan=False,separators=(',',':')).encode('utf-8')+b'\n'


def validate_imu(data,robot,sim_time):
    if not isinstance(data,dict) or data.get('calibrated') is not False:raise ProtocolError('invalid uncalibrated IMU state')
    if not isinstance(data.get('model'),str) or not isinstance(data.get('profile_md5'),str):raise ProtocolError('missing IMU model identity')
    number(data.get('gravity_z_mps2'),'world gravity')
    profiles=data.get('profiles');samples=data.get('samples');stats=data.get('stats')
    if not isinstance(profiles,list) or not 1<=len(profiles)<=4 or not isinstance(samples,list) or len(samples)>128 or not isinstance(stats,list) or len(stats)!=len(profiles):
        raise ProtocolError('invalid IMU batch size')
    ids=set()
    for p in profiles:
        if not isinstance(p,dict):raise ProtocolError('invalid IMU profile')
        name=identifier(p.get('id'))
        if name=='base' or name in ids:raise ProtocolError('duplicate or reserved IMU id')
        ids.add(name)
        if p.get('frame_id')!=robot+'/'+name+'_link' or p.get('parent_frame_id')!=robot+'/base_link':raise ProtocolError('invalid IMU frames')
        if not 1<=number(p.get('rate_hz'),'IMU rate')<=1000:raise ProtocolError('invalid IMU rate')
        pose(p.get('pose_in_base'))
        for key in ('accel_bias_mps2','gyro_bias_rad_s','accel_noise_std_mps2','gyro_noise_std_rad_s'):
            values=vector(p.get(key),3,key)
            if 'std' in key and any(x<0 for x in values):raise ProtocolError('negative IMU noise')
        integer(p.get('noise_seed'),'IMU seed',0,2147483647)
    seen_stats=set()
    for item in stats:
        if not isinstance(item,dict) or item.get('id') not in ids or item['id'] in seen_stats:raise ProtocolError('invalid IMU counters')
        seen_stats.add(item['id'])
        for key in ('measured_samples','missed_periods','dropped_samples','history_resets','invalid_samples'):integer(item.get(key),key)
    previous={}
    for s in samples:
        if not isinstance(s,dict) or s.get('id') not in ids:raise ProtocolError('unknown IMU sample id')
        seq=integer(s.get('seq'),'IMU sequence',1);stamp=integer(s.get('stamp_ns'),'IMU stamp')
        if stamp>sim_time:raise ProtocolError('IMU sample ahead of physics state')
        if not 0<number(s.get('integration_dt_s'),'IMU integration interval')<=3600:raise ProtocolError('invalid IMU integration interval')
        vector(s.get('angular_velocity_rad_s'),3,'IMU gyro');vector(s.get('specific_force_mps2'),3,'IMU acceleration')
        old=previous.get(s['id'])
        if old and (seq<=old[0] or stamp<=old[1]):raise ProtocolError('IMU batch not ordered')
        previous[s['id']]=(seq,stamp)


LIDAR_POINT = struct.Struct('<IfffBBBB')


def validate_lidar(data,payload,camera_bytes,robot,sim_time):
    if not isinstance(data,dict) or data.get('calibrated') is not False:raise ProtocolError('invalid uncalibrated LiDAR metadata')
    name=identifier(data.get('id'))
    if name=='base' or data.get('frame_id')!=robot+'/'+name+'_link' or data.get('parent_frame_id')!=robot+'/base_link':raise ProtocolError('invalid LiDAR frames')
    if not isinstance(data.get('model'),str) or not isinstance(data.get('profile_md5'),str):raise ProtocolError('missing LiDAR model identity')
    pose(data.get('pose_in_base'))
    if not 100<=number(data.get('rays_per_second'),'ray rate')<=200000 or not 1<=number(data.get('frame_hz'),'LiDAR frame rate')<=30:raise ProtocolError('invalid LiDAR rate')
    lo=number(data.get('min_range_m'),'minimum range');hi=number(data.get('max_range_m'),'maximum range')
    if not 0<lo<hi<=200:raise ProtocolError('invalid LiDAR range')
    low=number(data.get('elevation_min_rad'),'minimum elevation');high=number(data.get('elevation_max_rad'),'maximum elevation')
    if not -math.pi/2<=low<high<=math.pi/2:raise ProtocolError('invalid LiDAR elevation')
    for key in ('traced_rays','returns','missed_rays','dropped_scans','history_resets'):integer(data.get(key),key)
    if number(data.get('max_physics_step_s'),'physical time quantization')<0:raise ProtocolError('invalid LiDAR time quantization')
    scans=data.get('scans')
    if not isinstance(scans,list) or len(scans)>2:raise ProtocolError('invalid bounded LiDAR scans')
    offset=camera_bytes;previous=-1
    for scan in scans:
        if not isinstance(scan,dict):raise ProtocolError('invalid LiDAR scan')
        seq=integer(scan.get('seq'),'LiDAR sequence',1)
        start=integer(scan.get('start_ns'),'LiDAR start');end=integer(scan.get('end_ns'),'LiDAR end')
        n=integer(scan.get('point_count'),'LiDAR count',0,65536)
        length=integer(scan.get('byte_length'),'LiDAR bytes',0,65536*20)
        declared_offset=integer(scan.get('byte_offset'),'LiDAR offset',0,MAX_IMAGE)
        if seq<=previous or not start<=end<=sim_time or declared_offset!=offset or length!=n*20 or offset+length>len(payload):raise ProtocolError('invalid LiDAR payload layout/time')
        last_time=-1
        for dt,x,y,z,reflectivity,tag,line,pad in LIDAR_POINT.iter_unpack(memoryview(payload)[offset:offset+length]):
            if not all(math.isfinite(v) for v in (x,y,z)) or line>=4 or tag!=0 or pad!=0 or dt<last_time or start+dt>end+2:raise ProtocolError('invalid LiDAR return')
            if x*x+y*y+z*z>(hi+.001)**2:raise ProtocolError('LiDAR return exceeds configured range')
            last_time=dt
        offset+=length;previous=seq
    if offset!=len(payload):raise ProtocolError('unclaimed binary payload')

