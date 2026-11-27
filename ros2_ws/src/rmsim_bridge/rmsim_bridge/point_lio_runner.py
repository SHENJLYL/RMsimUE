"""Run the original estimator; a new simulator session gets a clean process/map."""
import datetime,json,math,os,subprocess,time
from pathlib import Path
from .native_transport import load_windows_fastrtps
load_windows_fastrtps()
import rclpy
from rclpy.node import Node
from rclpy.clock import Clock,ClockType
from rclpy.qos import QoSProfile
from std_msgs.msg import String
from ament_index_python.packages import get_package_prefix
from .protocol import identifier,pose,number,mount_pose
from .pointlio_units import si_acceleration_parameters
from .sentry_pointlio_source import pointlio_tuning,pointlio_source_snapshot

def multiply(a,b):
    x,y,z,w=a;u,v,t,s=b
    return [w*u+x*s+y*t-z*v,w*v-x*t+y*s+z*u,w*t+x*v-y*u+z*s,w*s-x*u-y*v-z*t]

def rotate(q,v):
    return multiply(multiply(q,[*v,0.]),[-q[0],-q[1],-q[2],q[3]])[:3]

def parameters(data,robot,imu_id,sentry_source='',*,source_tuning=None):
    tuning=pointlio_tuning(sentry_source) if source_tuning is None else dict(source_tuning)
    lidar=data['lidar'];imu=next(p for p in data['imu']['profiles'] if p['id']==imu_id)
    if (lidar.get('profile_schema') == 'rm_lidar_runtime_v3' and
            lidar['physical_model'].get('query_group_stride', 1) > 1):
        # The simulator already sampled complete four-channel groups. Taking
        # every eighth slot again would keep only one of those channels.
        tuning['point_filter_num'] = 1
    if lidar['parent_frame_id']!=imu['parent_frame_id']:raise ValueError('Point-LIO requires LiDAR and IMU on the same rigid parent')
    li=mount_pose(lidar,robot);im=mount_pose(imu,robot)
    q=im['rotation_xyzw'];inverse=[-q[0],-q[1],-q[2],q[3]]
    x,y,z,w=multiply(inverse,li['rotation_xyzw'])
    rotation=list(map(float,[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w),2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w),2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]))
    translation=list(map(float,rotate(inverse,[a-b for a,b in zip(li['position_m'],im['position_m'])])))
    gravity=number(data['imu']['gravity_z_mps2'],'world gravity')
    return {'use_sim_time':True,'common.lid_topic':'/'+robot+'/'+identifier(lidar['id'])+'/livox',
        'common.imu_topic':'/'+robot+'/'+imu_id+'/data_raw',
        'common.reliable_sensors':True,'common.imu_qos_depth':5,**si_acceleration_parameters(),
        'mapping.gravity':[0.,0.,gravity],'mapping.gravity_init':[0.,0.,gravity],
        'mapping.extrinsic_T':translation,'mapping.extrinsic_R':rotation,
        'mapping.imu_time_inte':1./number(imu['rate_hz'],'IMU rate'),
        'mapping.lidar_time_inte':1./number(lidar['frame_hz'],'LiDAR rate'),
        'filter_size_surf':.15,'filter_size_map':.15,'publish.tf_send_en':False,
        **tuning}

class PointLioRunner(Node):
    def __init__(self):
        super().__init__('rmsim_point_lio_runner')
        self.robot=identifier(self.declare_parameter('robot_id','aim_sentry').value)
        self.imu_id=identifier(self.declare_parameter('imu_id','imu').value)
        self.sentry_source=self.declare_parameter('sentry_source','').value
        self.source_snapshot=pointlio_source_snapshot(self.sentry_source)
        self.source_tuning=self.source_snapshot['parameters']
        self.source_identity={key:self.source_snapshot[key] for key in ('path','sha256')}
        base=Path(self.declare_parameter('output_directory','artifacts/localization/pointlio').value)
        self.output=base/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid()))
        self.output.mkdir(parents=True,exist_ok=False)
        self.state=None;self.received=0.;self.identity=None;self.child=None;self.log=None;self.starts=0;self.phase='waiting_for_sensors';self.last_error=''
        self.create_subscription(String,'/rmsim/'+self.robot+'/state',self.observe,QoSProfile(depth=1))
        self.status=self.create_publisher(String,'/rmsim/'+self.robot+'/localization',QoSProfile(depth=1))
        self.timer=self.create_timer(.1,self.poll,clock=Clock(clock_type=ClockType.STEADY_TIME))

    def observe(self,message):
        value=json.loads(message.data)
        if value.get('robot_id')!=self.robot:return
        self.state=value;self.received=time.monotonic()

    def stop(self):
        if self.child and self.child.poll() is None:
            self.child.terminate();self.child.wait(timeout=10)
        self.child=None
        if self.log:self.log.close();self.log=None

    def poll(self):
        ready=self.state and self.state.get('connected') and self.state.get('state_fresh') and time.monotonic()-self.received<2 and self.state.get('imu') and self.state.get('lidar')
        if self.child and self.child.poll() is not None:
            code=self.child.returncode;self.stop();raise RuntimeError('Original Point-LIO exited unexpectedly: '+str(code))
        if not ready:
            if time.monotonic()-self.received>2:self.stop()
            same_stream = (self.child and self.state and self.state.get('connected') and
                           (self.state.get('session'),self.state.get('epoch')) == self.identity)
            self.phase='waiting_for_stream' if same_stream else 'waiting_for_sensors'
        else:
            identity=(self.state['session'],self.state['epoch'])
            if identity!=self.identity or self.child is None:
                self.stop();config=parameters(self.state,self.robot,self.imu_id,source_tuning=self.source_tuning);self.starts+=1
                file=self.output/f'run-{self.starts:04d}.yaml'
                file.write_text(json.dumps({'/**':{'ros__parameters':config}},indent=2)+'\n',encoding='utf-8')
                prefix=Path(get_package_prefix('point_lio'));executable=prefix/'lib/point_lio'/('pointlio_mapping.exe' if os.name=='nt' else 'pointlio_mapping')
                self.log=(self.output/f'run-{self.starts:04d}.log').open('w',encoding='utf-8')
                options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
                self.child=subprocess.Popen([str(executable),'--ros-args','--params-file',str(prefix/'share/point_lio/config/mid360.yaml'),'--params-file',str(file)],stdout=self.log,stderr=subprocess.STDOUT,**options)
                self.identity=identity;self.get_logger().info('Started original Point-LIO for new sensor session; extrinsics derived from actual mounts.')
            self.phase='running'
        report=dict(phase=self.phase,starts=self.starts,identity=self.identity,pid=self.child.pid if self.child else None,
            reference_frame=self.robot+'/'+self.imu_id+'_link',
            sentry_source=self.sentry_source,source_filtering=self.source_tuning,source_identity=self.source_identity,
            reference_parent_frame=next((p['parent_frame_id'] for p in (self.state or {}).get('imu',{}).get('profiles',[]) if p['id']==self.imu_id),None),
            scope='original estimator; new sensor session restarts map and local origin; no truth input',output_directory=str(self.output))
        self.status.publish(String(data=json.dumps(report)))
        (self.output/'status.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')

    def destroy_node(self):
        self.stop();return super().destroy_node()

def main(args=None):
    rclpy.init(args=args);node=PointLioRunner()
    try:rclpy.spin(node)
    except KeyboardInterrupt:pass
    finally:node.destroy_node();rclpy.shutdown()
