"""Original Nav2 high-speed tracking and cancellation gate on import_lab.

The two fixed goals use the declared map: stage at (-8.5, 0), stop, then
measure the original Nav2 route to (8.5, 0). Ground truth is subscribed only
for independent measurement and the cancellation test trigger; it is never
published as localization, odometry, a transform, or a navigation command.
This gate complements the unchanged 14-check wave-route gate.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import math
import os
from pathlib import Path
import time

import yaml

ROOT=Path(__file__).resolve().parents[2]
STAGING_GOAL=(-8.5,0.0)
GOAL=(8.5,0.0)
STAGING_POSITION_TOLERANCE_M=.25
STAGING_STOP_SPEED_MPS=.05
STAGING_STOP_SECONDS=.5
SUSTAINED_FRACTION=.8
SUSTAINED_SECONDS=.3
TRACKING_TIME_ALLOWANCE=.10
TRACKING_DISTANCE_ALLOWANCE=.05
STOP_TIME_ALLOWANCE=.15
BRIDGE_PERIOD_BUDGET=.02
TARGET_TOLERANCE=1.e-6


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def segment_intersects_box(start,end,center,size,yaw,padding):
    """Conservative swept circle test against an oriented rectangular obstacle."""
    c,s=math.cos(yaw),math.sin(yaw)
    def local(point):
        x,y=point[0]-center[0],point[1]-center[1]
        return (c*x+s*y,-s*x+c*y)
    a,b=local(start),local(end)
    low,high=0.,1.
    for axis in range(2):
        half=size[axis]/2+padding
        delta=b[axis]-a[axis]
        if abs(delta)<1.e-12:
            if abs(a[axis])>half:
                return False
            continue
        enter,leave=sorted(((-half-a[axis])/delta,(half-a[axis])/delta))
        low=max(low,enter)
        high=min(high,leave)
        if low>high:
            return False
    return True


def lane_geometry(scene,start,end,swept_radius):
    """Validate real floor/wall/box geometry without changing collision."""
    floor=scene['floor']['size_m']
    boundary=scene['boundary']['thickness_m']
    wall_clearance=min(floor[axis]/2-boundary-swept_radius-abs(point[axis])
                       for point in (start,end) for axis in range(2))
    if wall_clearance<=0:
        raise ValueError('The test lane violates the declared floor/boundary clearance')
    obstacle_clearances=[]
    for obstacle in scene['obstacles']:
        arguments=(start,end,obstacle['center_m'],obstacle['size_m'],obstacle['yaw_rad'])
        if segment_intersects_box(*arguments,swept_radius):
            raise ValueError('The test lane intersects the swept footprint at '+obstacle['id'])
        # Additional box-local padding until the straight segment intersects.
        # The same conservative expanded-box test is used for acceptance.
        lower,upper=swept_radius,2*max(floor)
        for _ in range(40):
            middle=(lower+upper)/2
            if segment_intersects_box(*arguments,middle):upper=middle
            else:lower=middle
        obstacle_clearances.append({'id':obstacle['id'],
                                    'additional_box_padding_clearance_m':lower-swept_radius})
    return {'start_m':list(start),'goal_m':list(end),'length_m':math.dist(start,end),
            'swept_radius_m':swept_radius,'wall_clearance_m':wall_clearance,
            'boundary_inner_half_extents_m':[v/2-boundary for v in floor],
            'obstacle_clearances':obstacle_clearances,
            'clearance_method':'original conservative expanded oriented box and inner-wall test'}


def speed_lane_contract(scene,start,end,swept_radius,cap,accel,approach_distance):
    lane=lane_geometry(scene,start,end,swept_radius)
    threshold=SUSTAINED_FRACTION*cap
    minimum_length=threshold*threshold/(2*accel)+threshold*SUSTAINED_SECONDS
    available=lane['length_m']-approach_distance
    if minimum_length>=available:
        raise ValueError('The declared lane cannot achieve the source speed threshold before approach slowdown')
    lane.update(source_theoretical_minimum_length_m=minimum_length,
                approach_velocity_scaling_dist_m=approach_distance,
                length_before_approach_m=available,
                theoretical_length_formula='threshold^2/(2*source_accel) + threshold*sustained_seconds')
    return lane


def staging_arrival_evidence(position,status,speed,stationary_seconds):
    error=math.dist(position[:2],STAGING_GOAL)
    return {'actual_position_m':list(position),'goal_m':list(STAGING_GOAL),
            'action_status':status,'action_succeeded':status==4,
            'actual_error_m':error,'position_tolerance_m':STAGING_POSITION_TOLERANCE_M,
            'within_position_tolerance':error<=STAGING_POSITION_TOLERANCE_M,
            'speed_mps':speed,'stop_speed_limit_mps':STAGING_STOP_SPEED_MPS,
            'stationary_seconds':stationary_seconds,'required_stationary_seconds':STAGING_STOP_SECONDS,
            'stopped':speed is not None and speed<STAGING_STOP_SPEED_MPS
                and stationary_seconds>=STAGING_STOP_SECONDS}


def formal_rows(rows,start_stamp):
    """Exclude staging and delayed messages whose header predates the formal goal."""
    return [row for row in rows if row.get('phase')=='formal' and row['stamp']>=start_stamp]


def source_contract(session):
    provenance_path=session/'runtime-provenance.json'
    provenance_bytes=provenance_path.read_bytes()
    provenance=json.loads(provenance_bytes.decode('utf-8-sig'))
    scene_info=provenance['scene']
    scene_path=Path(scene_info['scene_file'])
    scene=json.loads(scene_path.read_text(encoding='utf-8-sig'))
    if scene_info['scene']!='import_lab' or scene['kind']!='rm_flat_map' or scene['id']!='import_lab':
        raise ValueError('This high-speed gate requires a fresh import_lab console session')
    if digest(scene_path)!=scene_info['scene_sha256']:
        raise ValueError('The session scene changed after launch')
    declared=json.loads((ROOT/'assets/maps/import_lab/map.json').read_text(encoding='utf-8-sig'))
    if scene!=declared:
        raise ValueError('The live fixture must match the declared import_lab geometry and spawns')
    spawn=next(s for s in scene['spawns'] if s['id']=='default')
    spawn_start=tuple(spawn['position_m'][:2])
    if spawn_start!=(0.,0.) or abs(spawn['yaw_rad'])>1.e-9:
        raise ValueError('The declared forward high-speed lane origin changed')
    # Covers the current 0.52 m square footprint by its circumscribed circle,
    # plus 0.10 m; collision geometry and floor are read from the actual fixture.
    swept_radius=math.sqrt(2)*.26+.10
    staging_lane=lane_geometry(scene,spawn_start,STAGING_GOAL,swept_radius)
    source=Path(provenance['navigation_source'])
    source_hash=digest(source)
    if source_hash!=provenance['source_control_config_sha256']:
        raise ValueError('The navigation source changed after the console launched')
    data=yaml.safe_load(source.read_text(encoding='utf-8-sig'))
    controller=data['controller_server']['ros__parameters']
    smooth=data['velocity_smoother']['ros__parameters']
    cap=min(float(controller['FollowPath']['v_linear_max']),float(smooth['max_velocity'][0]))
    accel=min(float(v) for v in smooth['max_accel'][:2])
    decel=min(-float(v) for v in smooth['max_decel'][:2])
    controller_hz=float(controller['controller_frequency'])
    smoother_hz=float(smooth['smoothing_frequency'])
    step=float(provenance['sim_step_seconds'])
    if any(not math.isfinite(v) or v<=0 for v in (cap,accel,decel,controller_hz,smoother_hz,step)):
        raise ValueError('Invalid source speed, acceleration, deceleration, frequency or physics step')
    drive_path=ROOT/'assets/vehicles/aim_sentry/drive-profile.json'
    if digest(drive_path)!=provenance['drive_profile_sha256']:
        raise ValueError('The physical vehicle profile changed after launch')
    manual_cap=float(json.loads(drive_path.read_text(encoding='utf-8-sig'))['control']['MaxForwardSpeedMps'])
    if abs(cap-manual_cap)>1.e-9:
        raise ValueError('The speed gate requires the selected source to use the manual forward ceiling')
    threshold=SUSTAINED_FRACTION*cap
    latency=1/controller_hz+1/smoother_hz+2*BRIDGE_PERIOD_BUDGET+step
    approach_distance=float(controller['FollowPath']['approach_velocity_scaling_dist'])
    formal_lane=speed_lane_contract(scene,STAGING_GOAL,GOAL,swept_radius,cap,accel,approach_distance)
    return dict(provenance=provenance,
                runtime_provenance_path=str(provenance_path),
                runtime_provenance_sha256=hashlib.sha256(provenance_bytes).hexdigest(),
                scene_sha256=digest(scene_path),map_id=scene['id'],
                source_path=str(source),source_sha256=source_hash,source_cap_mps=cap,
                source_accel_mps2=accel,source_decel_mps2=decel,
                source_smoother_max_velocity=smooth['max_velocity'],
                source_smoother_max_accel=smooth['max_accel'],
                source_smoother_max_decel=smooth['max_decel'],
                speed_threshold_mps=threshold,sustained_seconds=SUSTAINED_SECONDS,
                fixture_version=2,goal=list(GOAL),start=list(STAGING_GOAL),
                spawn_start=list(spawn_start),staging_goal=list(STAGING_GOAL),
                staging_position_tolerance_m=STAGING_POSITION_TOLERANCE_M,
                staging_stop_speed_mps=STAGING_STOP_SPEED_MPS,staging_stop_seconds=STAGING_STOP_SECONDS,
                staging_lane=staging_lane,formal_lane=formal_lane,
                approach_velocity_scaling_dist_m=approach_distance,swept_radius_m=swept_radius,
                cancellation_latency_budget_s=latency,
                latency_components_s={'controller_cycle':1/controller_hz,'smoother_cycle':1/smoother_hz,
                    'bridge_roundtrip_budget':2*BRIDGE_PERIOD_BUDGET,'physics_step':step},
                tracking_time_allowance_s=TRACKING_TIME_ALLOWANCE,
                tracking_distance_allowance_m=TRACKING_DISTANCE_ALLOWANCE,
                stop_time_allowance_s=STOP_TIME_ALLOWANCE)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--domain',type=int,default=71)
    parser.add_argument('--validate-only',action='store_true')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    report={'schema_version':2,'status':'running','script_sha256':digest(__file__),'scope':'original Nav2 staging and formal actions; source-owned speed and smoothing; formal actual >=80% cap sustained for 0.3 s; truth only for evaluation','checks':[],'actions':[]}
    trace={'schema_version':2,'truth':[],'commands':[],'drive':[],'plans':[],'events':[],'actions':report['actions']}
    node=client=active=None
    initialized=False
    current={'truth':None,'state':None,'nav':None,'plan_points':0,'speed':None,'phase':'startup'}
    identity=None
    baseline_rejections=None
    baseline_imu=baseline_scans=0
    result=None
    recent=deque()
    sustained_start=None
    stopped_start=None
    cancel_stamp=None
    stop_record=None
    staging_stopped_start=None
    staging_stop_record=None
    formal_start_stamp=None

    def save():
        report['last_navigation']=current['nav']
        report['last_truth']=current['truth']
        (args.output/'verification.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')

    def check(name,passed,**details):
        report['checks'].append(dict(name=name,passed=bool(passed),**details))
        save()
        if not passed:
            raise AssertionError(name)
        print('PASS',name,flush=True)

    try:
        contract=source_contract(args.session.resolve())
        report['contract']={k:v for k,v in contract.items() if k!='provenance'}
        report['runtime_provenance_sha256']=contract['runtime_provenance_sha256']
        trace['metadata']={'script_sha256':report['script_sha256'],
                           'runtime_provenance_sha256':contract['runtime_provenance_sha256'],
                           'source_sha256':contract['source_sha256'],'scene_sha256':contract['scene_sha256']}
        if args.validate_only:
            report['status']='configuration_validated_only'
            save()
            print('Configuration validated; no ROS context or navigation action started')
            return 0
        os.environ['ROS_DOMAIN_ID']=str(args.domain)
        from rmsim_bridge.native_transport import configure_navigation_transport
        configure_navigation_transport()
        import rclpy
        from rclpy.node import Node
        from rclpy.action import ActionClient
        from rclpy.qos import qos_profile_sensor_data
        from geometry_msgs.msg import PoseStamped,TwistStamped
        from nav_msgs.msg import Path as NavPath
        from nav2_msgs.action import NavigateToPose
        from std_msgs.msg import String
        rclpy.init()
        initialized=True
        node=Node('rmsim_navigation_top_speed_gate')

        def truth(message):
            nonlocal sustained_start,stopped_start,stop_record,staging_stopped_start,staging_stop_record
            stamp=message.header.stamp.sec+message.header.stamp.nanosec*1.e-9
            p=message.pose.position
            q=message.pose.orientation
            row={'stamp':stamp,'stamp_ns':message.header.stamp.sec*1_000_000_000+message.header.stamp.nanosec,
                 'wall':time.monotonic(),'position':[p.x,p.y,p.z],
                 'orientation_xyzw':[q.x,q.y,q.z,q.w],'frame_id':message.header.frame_id,
                 'phase':current['phase'],'session_identity':identity}
            old=current['truth']
            if old and stamp<=old['stamp']:
                return
            current['truth']=row
            trace['truth'].append(row)
            if old and stamp-old['stamp']>.15:
                sustained_start=stopped_start=None
                staging_stopped_start=staging_stop_record=None
                recent.clear()
            recent.append(row)
            while len(recent)>2 and stamp-recent[1]['stamp']>.1:
                recent.popleft()
            elapsed=stamp-recent[0]['stamp']
            if elapsed<.05:
                return
            speed=math.dist(recent[0]['position'][:2],row['position'][:2])/elapsed
            current['speed']=speed
            row['speed_mps']=speed
            is_formal=current['phase']=='formal' and formal_start_stamp is not None and stamp>=formal_start_stamp
            if is_formal:
                report['actual_peak_speed_mps']=max(report.get('actual_peak_speed_mps',0.),speed)
            elif current['phase']=='staging':
                report['staging_peak_speed_mps']=max(report.get('staging_peak_speed_mps',0.),speed)
            if is_formal and speed>=contract['speed_threshold_mps']:
                if sustained_start is None:
                    sustained_start=stamp
            else:
                sustained_start=None
            if current['phase']=='staging' and speed<STAGING_STOP_SPEED_MPS:
                if staging_stopped_start is None:staging_stopped_start=stamp
                if stamp-staging_stopped_start>=STAGING_STOP_SECONDS:
                    staging_stop_record=dict(stamp=staging_stopped_start,confirmed_stamp=stamp,
                                             position=row['position'],speed_mps=speed)
            else:
                staging_stopped_start=staging_stop_record=None
            if is_formal and cancel_stamp is not None and speed<.05:
                if stopped_start is None:
                    stopped_start=stamp
                if stamp-stopped_start>=.5 and stop_record is None:
                    stop_record=dict(stamp=stopped_start,confirmed_stamp=stamp,position=row['position'])
            else:
                stopped_start=None

        def state(message):
            value=json.loads(message.data)
            current['state']=value
            stamp=value.get('sim_time_ns',0)*1.e-9
            if trace['drive'] and stamp<=trace['drive'][-1]['stamp']:
                return
            drive=value.get('drive')
            if isinstance(drive,dict):
                trace['drive'].append({'stamp':stamp,'phase':current['phase'],'session_identity':identity,
                                       'drive':drive,'command':value.get('command',{})})

        def command(message):
            t=message.header.stamp.sec+message.header.stamp.nanosec*1.e-9
            v=message.twist
            trace['commands'].append({'stamp':t,'wall':time.monotonic(),'linear':[v.linear.x,v.linear.y],
                                      'yaw':v.angular.z,'frame_id':message.header.frame_id,
                                      'phase':current['phase'],'session_identity':identity})

        def plan(message):
            stamp=message.header.stamp.sec+message.header.stamp.nanosec*1.e-9
            trace['plans'].append({'stamp':stamp,'phase':current['phase'],'session_identity':identity,
                                   'points':len(message.poses),'frame_id':message.header.frame_id,
                                   'end_position_m':([message.poses[-1].pose.position.x,
                                                      message.poses[-1].pose.position.y] if message.poses else None)})
            if current['phase']=='formal' and formal_start_stamp is not None and stamp>=formal_start_stamp:
                current['plan_points']=max(current['plan_points'],len(message.poses))

        node.create_subscription(PoseStamped,'/rmsim/evaluation/aim_sentry/ground_truth',truth,qos_profile_sensor_data)
        node.create_subscription(String,'/rmsim/aim_sentry/state',state,1)
        node.create_subscription(String,'/rmsim/aim_sentry/navigation',lambda m:current.update(nav=json.loads(m.data)),1)
        node.create_subscription(TwistStamped,'/aim_sentry/cmd_vel',command,1)
        node.create_subscription(NavPath,'/plan',plan,1)
        client=ActionClient(node,NavigateToPose,'navigate_to_pose')

        def pump_until(condition,wall_timeout,sim_timeout=None):
            wall_start=time.monotonic()
            sim_start=current['truth']['stamp'] if current['truth'] else None
            while not condition():
                if time.monotonic()-wall_start>wall_timeout:
                    raise TimeoutError('Native top-speed test wall deadline exceeded')
                if sim_timeout is not None and sim_start is not None and current['truth'] and current['truth']['stamp']-sim_start>sim_timeout:
                    raise TimeoutError('Native top-speed test simulation deadline exceeded')
                if identity is not None and current['nav'] and current['nav'].get('identity')!=identity:
                    raise RuntimeError('Sensor/navigation session changed during the speed gate')
                rclpy.spin_once(node,timeout_sec=.01)

        pump_until(lambda:current['nav'] and current['nav'].get('phase')=='running'
                   and current['truth'] and current['state'] and current['state'].get('state_fresh')
                   and client.server_is_ready(),180)
        identity=list(current['nav']['identity'])
        session_state=json.loads((args.session/'status.json').read_text(encoding='utf-8-sig'))
        check('observer_matches_the_requested_session',session_state.get('navigation',{}).get('identity')==identity
              and session_state.get('ros_domain_id')==args.domain and session_state.get('phase')!='stopped')
        nav=current['nav']
        state0=current['state']
        check('fresh_import_lab_native_stack',nav['map_id']=='import_lab' and state0.get('map',{}).get('id')=='import_lab'
              and math.dist(current['truth']['position'][:2],contract['spawn_start'])<.3,
              identity=identity,start=current['truth']['position'])
        check('source_speed_and_smoother_selected',
              nav.get('control_source',{}).get('source_sha256')==contract['source_sha256']
              and abs(nav['navigation_velocity_limit_mps']-contract['source_cap_mps'])<1.e-9
              and nav['smoother_max_velocity']==contract['source_smoother_max_velocity']
              and nav['smoother_max_accel']==contract['source_smoother_max_accel']
              and nav['smoother_max_decel']==contract['source_smoother_max_decel'])
        drive=state0.get('drive',{})
        fields=('applied_requested_world_linear_m_s','filtered_world_linear_m_s',
                'applied_requested_yaw_rad_s','filtered_yaw_rad_s','applied_velocity_command_mode')
        check('synchronized_drive_telemetry_available',all(key in drive for key in fields))
        baseline_rejections=(state0.get('command',{}).get('rejected_count',0),state0.get('rejected_commands',0))

        def send_original_goal(phase,xy,accepted_check):
            nonlocal active,result
            record={'phase':phase,'session_identity':list(identity),'frame_id':'aim_sentry/map',
                    'goal_position_m':[xy[0],xy[1],0.],'goal_orientation_xyzw':[0.,0.,0.,1.],
                    'requested_stamp':current['truth']['stamp'],'requested_wall':time.monotonic()}
            report['actions'].append(record)
            trace['events'].append({'event':'goal_requested',**record})
            goal=NavigateToPose.Goal()
            goal.pose=PoseStamped()
            goal.pose.header.frame_id=record['frame_id']
            goal.pose.pose.position.x=xy[0]
            goal.pose.pose.position.y=xy[1]
            goal.pose.pose.orientation.w=1.
            future=client.send_goal_async(goal)
            pump_until(future.done,20)
            active=future.result()
            record.update(accepted=active.accepted,goal_id_hex=bytes(active.goal_id.uuid).hex(),
                          accepted_stamp=current['truth']['stamp'],accepted_wall=time.monotonic())
            trace['events'].append({'event':'goal_accepted','phase':phase,
                                    'session_identity':list(identity),'goal_id_hex':record['goal_id_hex'],
                                    'stamp':record['accepted_stamp'],'wall':record['accepted_wall'],
                                    'accepted':active.accepted})
            check(accepted_check,active.accepted,goal=list(xy),goal_id_hex=record['goal_id_hex'],
                  session_identity=list(identity))
            result=active.get_result_async()
            return record

        def record_action_result(record):
            response=result.result()
            record.update(result_status=response.status,result_stamp=current['truth']['stamp'],
                          result_wall=time.monotonic(),result_position_m=list(current['truth']['position']))
            trace['events'].append({'event':'goal_result','phase':record['phase'],
                                    'goal_id_hex':record['goal_id_hex'],'session_identity':list(identity),
                                    'stamp':record['result_stamp'],'wall':record['result_wall'],
                                    'status':response.status})
            save()
            return response.status

        current['phase']='staging'
        staging_action=send_original_goal('staging',STAGING_GOAL,'staging_original_nav2_goal_accepted')
        pump_until(result.done,120,60)
        staging_status=record_action_result(staging_action)
        check('staging_original_nav2_action_succeeded',staging_status==4,status=staging_status)
        # Confirm an entire stationary window after action completion.
        staging_stopped_start=staging_stop_record=None
        pump_until(lambda:staging_stop_record is not None,15,5.)
        stationary_seconds=staging_stop_record['confirmed_stamp']-staging_stop_record['stamp']
        staging=staging_arrival_evidence(current['truth']['position'],staging_status,
                                        current['speed'],stationary_seconds)
        staging.update(session_identity=list(identity),goal_id_hex=staging_action['goal_id_hex'],
                       stopped_start_stamp=staging_stop_record['stamp'],
                       stopped_confirmed_stamp=staging_stop_record['confirmed_stamp'])
        report['staging']=staging
        check('staging_actual_position_within_original_25cm',staging['within_position_tolerance'],
              error_m=staging['actual_error_m'],tolerance_m=STAGING_POSITION_TOLERANCE_M)
        check('staging_vehicle_stationary_before_formal_goal',staging['stopped'],
              speed_mps=staging['speed_mps'],stationary_seconds=stationary_seconds)
        scene_path=Path(contract['provenance']['scene']['scene_file'])
        if digest(scene_path)!=contract['scene_sha256']:
            raise ValueError('The declared collision scene changed during staging')
        scene=json.loads(scene_path.read_text(encoding='utf-8-sig'))
        actual_start=list(current['truth']['position'])
        measured_lane=speed_lane_contract(scene,actual_start[:2],GOAL,contract['swept_radius_m'],
                                          contract['source_cap_mps'],contract['source_accel_mps2'],
                                          contract['approach_velocity_scaling_dist_m'])
        report['measured_lane']=measured_lane
        check('staged_lane_has_source_speed_and_collision_clearance',True,
              declared_length_m=contract['formal_lane']['length_m'],actual_length_m=measured_lane['length_m'],
              theoretical_minimum_length_m=measured_lane['source_theoretical_minimum_length_m'],
              wall_clearance_m=measured_lane['wall_clearance_m'])

        # Formal sampling begins after staging. Staging speed/commands/plans remain
        # in the raw trace with their own phase and cannot satisfy a formal gate.
        formal_start_stamp=current['truth']['stamp']
        report['formal_measurement']={'session_identity':list(identity),'start_stamp':formal_start_stamp,
                                     'start_wall':time.monotonic(),'actual_start_position_m':actual_start,
                                     'measurement_phase':'formal'}
        recent.clear()
        sustained_start=stopped_start=stop_record=cancel_stamp=None
        current.update(phase='formal',speed=None,plan_points=0)
        report['actual_peak_speed_mps']=0.
        formal_state=current['state']
        baseline_imu=next((v['measured_samples'] for v in formal_state.get('imu',{}).get('stats',[]) if v['id']=='mid360_imu'),0)
        baseline_scans=formal_state.get('lidar',{}).get('published_scans',0)
        formal_action=send_original_goal('formal',GOAL,'original_nav2_goal_accepted')
        report['formal_measurement']['goal_id_hex']=formal_action['goal_id_hex']
        def sustained_or_finished():
            return result.done() or (sustained_start is not None and current['truth']['stamp']-sustained_start>=SUSTAINED_SECONDS)
        pump_until(sustained_or_finished,120,30)
        if result.done():record_action_result(formal_action)
        achieved=sustained_start is not None and current['truth']['stamp']-sustained_start>=SUSTAINED_SECONDS
        report['formal_measurement']['sustained']={'start_stamp':sustained_start,
                                                  'confirmed_stamp':current['truth']['stamp'],
                                                  'threshold_mps':contract['speed_threshold_mps'],
                                                  'required_duration_s':SUSTAINED_SECONDS}
        check('actual_speed_sustained_at_source_threshold',achieved and not result.done(),
              required_speed_mps=contract['speed_threshold_mps'],required_duration_s=SUSTAINED_SECONDS,
              observed_speed_mps=current['speed'],peak_mps=report.get('actual_peak_speed_mps',0.),
              goal_finished_before_threshold=result.done())
        check('original_planner_generated_route',current['plan_points']>2,points=current['plan_points'])

        cancel_origin=list(current['truth']['position'])
        cancel_stamp=current['truth']['stamp']
        v0=max(r.get('speed_mps',0.) for r in formal_rows(trace['truth'],formal_start_stamp)
               if cancel_stamp-r['stamp']<=.1)
        delay=contract['cancellation_latency_budget_s']
        accel=contract['source_accel_mps2']
        decel=contract['source_decel_mps2']
        reaction_speed=min(contract['source_cap_mps'],v0+accel*delay)
        distance_bound=v0*delay+.5*accel*delay*delay+reaction_speed*reaction_speed/(2*decel)+v0*TRACKING_TIME_ALLOWANCE+TRACKING_DISTANCE_ALLOWANCE
        time_bound=delay+reaction_speed/decel+STOP_TIME_ALLOWANCE
        report['cancellation']={'stamp':cancel_stamp,'initial_position':cancel_origin,'initial_speed_mps':v0,
            'reaction_speed_bound_mps':reaction_speed,'distance_bound_m':distance_bound,'time_bound_s':time_bound,
            'distance_formula':'v0*delay + 0.5*a*delay^2 + reaction_speed^2/(2*source_decel) + v0*0.10 + 0.05',
            'time_formula':'delay + reaction_speed/source_decel + 0.15',
            'allowance_scope':'predeclared integration acceptance budgets, not measured hardware calibration'}
        trace['events'].append({'event':'cancel_requested','phase':'formal','session_identity':list(identity),
                                'goal_id_hex':formal_action['goal_id_hex'],'stamp':cancel_stamp,'wall':time.monotonic()})
        cancel=active.cancel_goal_async()
        pump_until(cancel.done,20)
        check('cancel_request_accepted',bool(cancel.result().goals_canceling))
        pump_until(result.done,20)
        formal_status=record_action_result(formal_action)
        check('original_nav2_action_cancelled',formal_status==5,status=formal_status)
        pump_until(lambda:stop_record is not None,20,time_bound+1.)
        stopped_samples=[r for r in formal_rows(trace['truth'],formal_start_stamp)
                         if cancel_stamp<=r['stamp']<=stop_record['confirmed_stamp']]
        distance=sum(math.dist(a['position'][:2],b['position'][:2]) for a,b in zip(stopped_samples,stopped_samples[1:]))
        elapsed=stop_record['stamp']-cancel_stamp
        report['cancellation'].update(actual_stop_distance_m=distance,actual_stop_time_s=elapsed,
                                      stop_confirmed_stamp=stop_record['confirmed_stamp'])
        check('high_speed_cancel_respects_source_derived_bounds',distance<=distance_bound and elapsed<=time_bound,
              distance_m=distance,distance_bound_m=distance_bound,time_s=elapsed,time_bound_s=time_bound)
        stable_origin=list(current['truth']['position'])
        stable_stamp=current['truth']['stamp']
        pump_until(lambda:current['truth']['stamp']-stable_stamp>=2.,15,3.)
        drift=math.dist(stable_origin[:2],current['truth']['position'][:2])
        check('cancelled_vehicle_remains_stationary',drift<.02,drift_m=drift,observation_sim_seconds=2.)

        formal_truth=formal_rows(trace['truth'],formal_start_stamp)
        formal_drive=formal_rows(trace['drive'],formal_start_stamp)
        formal_commands=formal_rows(trace['commands'],formal_start_stamp)
        report['actual_peak_speed_mps']=max((r.get('speed_mps',0.) for r in formal_truth),default=0.)
        report['formal_measurement'].update(end_stamp=current['truth']['stamp'],end_wall=time.monotonic(),
                                            truth_samples=len(formal_truth),drive_samples=len(formal_drive),
                                            command_samples=len(formal_commands),
                                            actual_peak_speed_mps=report['actual_peak_speed_mps'])
        tracking=[]
        for row in formal_drive:
            d=row['drive']
            if not all(key in d for key in fields):
                continue
            desired=d['applied_requested_world_linear_m_s']
            if math.hypot(desired[0],desired[1])<=.2:
                continue
            linear_error=math.dist(desired[:2],d['filtered_world_linear_m_s'][:2])
            yaw_error=abs(d['applied_requested_yaw_rad_s']-d['filtered_yaw_rad_s'])
            tracking.append((linear_error,yaw_error,d['applied_velocity_command_mode']))
        check('source_command_reaches_wheel_target_without_second_shaper',
              len(tracking)>=20 and all(a<=TARGET_TOLERANCE and b<=TARGET_TOLERANCE and mode=='external_shaped_velocity'
                                        for a,b,mode in tracking),
              samples=len(tracking),max_linear_error_mps=max((a for a,b,m in tracking),default=None),
              max_yaw_error_rad_s=max((b for a,b,m in tracking),default=None),tolerance=TARGET_TOLERANCE)
        command_peak=max((math.hypot(*r['linear']) for r in formal_commands),default=0.)
        report['formal_measurement']['command_peak_speed_mps']=command_peak
        check('source_command_cap_preserved',command_peak<=contract['source_cap_mps']+1.e-5,
              source_cap_mps=contract['source_cap_mps'],observed_command_peak_mps=command_peak)
        last=current['state']
        rejections=(last.get('command',{}).get('rejected_count',0),last.get('rejected_commands',0))
        last_imu=next((v['measured_samples'] for v in last.get('imu',{}).get('stats',[]) if v['id']=='mid360_imu'),0)
        last_scans=last.get('lidar',{}).get('published_scans',0)
        check('mounted_imu_and_lidar_continued_sampling',last_imu-baseline_imu>=200 and last_scans-baseline_scans>=10,
              measured_imu_samples=last_imu-baseline_imu,published_lidar_scans=last_scans-baseline_scans)
        check('session_and_command_acceptance_preserved',
              current['nav']['identity']==identity and last.get('state_fresh') and rejections==baseline_rejections
              and not last.get('aim',{}).get('trigger_active'),
              rejections_before=baseline_rejections,rejections_after=rejections)
        report['status']='passed'
    except Exception as error:
        report.update(status='failed',error=repr(error))
        print('TOP_SPEED_GATE_FAILED',repr(error),flush=True)
    finally:
        if active is not None and active.accepted and initialized and (result is None or not result.done()):
            try:
                cancel=active.cancel_goal_async()
                deadline=time.monotonic()+5
                while not cancel.done() and time.monotonic()<deadline:
                    rclpy.spin_once(node,timeout_sec=.01)
                trace['events'].append({'event':'cleanup_cancel_requested','phase':current['phase'],
                                        'session_identity':identity,'goal_id_hex':bytes(active.goal_id.uuid).hex(),
                                        'wall':time.monotonic(),'request_completed':cancel.done()})
                deadline=time.monotonic()+5
                while result is not None and not result.done() and time.monotonic()<deadline:
                    rclpy.spin_once(node,timeout_sec=.01)
                if result is not None and result.done():
                    record_action_result(report['actions'][-1])
            except Exception as error:
                report['cleanup_cancel_error']=repr(error)
        trace_path=args.output/'trace.json'
        trace_path.write_text(json.dumps(trace,allow_nan=False),encoding='utf-8')
        report['trace_sha256']=digest(trace_path)
        save()
        if client is not None:
            client.destroy()
        if node is not None:
            node.destroy_node()
        if initialized:
            rclpy.shutdown()
    print('NATIVE_TOP_SPEED',report['status'],len(report['checks']),flush=True)
    return 0 if report['status']=='passed' else 2


if __name__=='__main__':
    raise SystemExit(main())
