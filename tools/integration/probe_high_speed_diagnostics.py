"""Native sentry-console action/route gate; truth is an independent observer only."""
from __future__ import annotations
import argparse
import hashlib
import sys
import json
import math
import os
from pathlib import Path
import statistics
import time


def vector_derivative(previous_stamp, previous_vector, stamp, vector):
    """A timestamped finite difference; repeated/backward times are unobservable."""
    if previous_stamp is None or stamp is None or previous_vector is None:
        return None
    delta = stamp - previous_stamp
    if not math.isfinite(delta) or delta <= 0:
        return None
    if len(previous_vector) != len(vector):
        raise ValueError('Derivative vector dimensions differ')
    values = [(new-old)/delta for old, new in zip(previous_vector, vector)]
    return values if all(math.isfinite(x) for x in values) else None


def header_record(header):
    stamp_ns = int(header.stamp.sec)*1_000_000_000 + int(header.stamp.nanosec)
    return {'frame_id':header.frame_id,'stamp_ns':stamp_ns,'stamp_s':stamp_ns*1.e-9}


def stamped_pose_record(header, pose):
    p,q=pose.position,pose.orientation
    return {**header_record(header),'position_m':[p.x,p.y,p.z],
            'orientation_xyzw':[q.x,q.y,q.z,q.w]}


def scan_number(value):
    """Keep LaserScan index and IEEE nonfinite meaning in strict JSON."""
    value=float(value)
    if math.isnan(value):return 'nan'
    if math.isinf(value):return 'inf' if value>0 else '-inf'
    return value


def precision_diagnostic(samples, navigation_status, identity, provenance_sha256, trace_sha256):
    """Independent endpoint-only diagnostic; never changes the original action gate."""
    checks=[]
    for mode in ('single_goal','waypoints'):
        matches=[row for row in samples if row.get('mode')==mode]
        valid=(len(matches)==1 and matches[0].get('action_status')==4
               and not matches[0].get('missed_waypoints')
               and isinstance(matches[0].get('actual_error_m'),(int,float))
               and math.isfinite(matches[0]['actual_error_m'])
               and 0<=matches[0]['actual_error_m']<=.10)
        checks.append({'name':mode+'_actual_error_at_most_0p10m','passed':valid,
                       'actual_error_m':matches[0].get('actual_error_m') if len(matches)==1 else None,
                       'endpoint_samples':len(matches)})
    checks.append({'name':'precision_endpoints_have_same_native_session',
                   'passed':bool(identity) and all(
                       [row.get('session'),row.get('epoch')]==list(identity) for row in samples)
                       and len(samples)==2})
    checks.append({'name':'precision_diagnostic_bound_to_runtime_and_raw_trace',
                   'passed':bool(provenance_sha256) and bool(trace_sha256)})
    return {'status':'passed' if all(row['passed'] for row in checks) else 'failed',
            'acceptance_scope':'independent endpoint precision diagnostic; original 14 checks and process exit are unchanged',
            'actual_xy_tolerance_m':.10,'original_xy_tolerance_m':.25,
            'scope':'single-goal endpoint and final waypoint endpoint only; intermediate waypoint precision is not inferred',
            'navigation_gate_status':navigation_status,'session_identity':identity,
            'runtime_provenance_sha256':provenance_sha256,'trace_sha256':trace_sha256,
            'checks':checks,'endpoints':samples}


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--domain',type=int,default=71)
    p.add_argument('--capture-localization-clouds',action='store_true',help='Diagnostic only: bounded raw registered/terrain point clouds at the two precision endpoints')
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    os.environ['ROS_DOMAIN_ID']=str(args.domain)
    from rmsim_bridge.native_transport import configure_navigation_transport
    configure_navigation_transport()
    import rclpy
    from rclpy.node import Node
    from rclpy.action import ActionClient
    from rclpy.qos import qos_profile_sensor_data, QoSProfile, ReliabilityPolicy, DurabilityPolicy
    from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, Twist, TwistStamped
    from nav_msgs.msg import Path as NavPath, Odometry
    from nav2_msgs.action import NavigateToPose,FollowWaypoints
    from sensor_msgs.msg import PointCloud2,Imu,LaserScan
    from tf2_msgs.msg import TFMessage
    from std_msgs.msg import String
    from rosgraph_msgs.msg import Clock
    rclpy.init();node=Node('rmsim_sentry_navigation_acceptance')
    current={'truth':None,'nav':None,'diag':None,'path_points':0,'clouds':0,'imu':0,'feedback':[]}
    trajectory=[];following=[];joint_yaws=[];active=None;clients=[];estimates=[];commands=[];imu_samples=[];sensor_status=[];cloud_stamps=[];last_sample=[-1.0];current.update(amcl=None, odom=None)
    performance_trace=[];motion_samples=[];cancel_events=[]
    plan_samples=[];goal_requests=[];goal_events=[];amcl_poses=[];odometry_poses=[]
    selected_transforms=[];latest_transforms={};obstacle_scans=[];truth_poses=[];precision_samples=[]
    current.update(goal_request=None,latest_plan=None,amcl_detail=None,odom_detail=None,truth_pose=None)
    provenance_path=args.output.parent/'runtime-provenance.json'
    provenance=json.loads(provenance_path.read_text(encoding='utf-8-sig')) if provenance_path.is_file() else {}
    provenance_sha256=hashlib.sha256(provenance_path.read_bytes()).hexdigest() if provenance else None
    command_streams={'controller':[],'smoothed':[],'vehicle':[]}
    current.update(clock_ns=0,truth_stamp=None,latest_motion=None,probe_phase='waiting')
    localization_clouds = None
    if args.capture_localization_clouds:
        from localization_cloud_capture import LocalizationCloudCapture, TOPICS
        localization_clouds = LocalizationCloudCapture()
    report={'status':'running','scope':'native original Nav2 actions, moving gimbal-mounted Mid-360 and actual joints; truth used only for evaluation','checks':[],
            'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'command':[sys.executable,*sys.argv],
            'trace_scope':'full bridge diagnostic and command receipt samples; derived velocities/accelerations are independent finite-difference observations',
            'localization_cloud_capture':{'enabled':bool(args.capture_localization_clouds),
                'acceptance_scope':'optional diagnostic capture; normal acceptance leaves this disabled'}}
    def context():
        d=current['diag'] or {};request=current.get('goal_request') or {}
        return {'received_monotonic_s':time.monotonic(),'latest_clock_ns':current['clock_ns'],
                'bridge_sim_time_ns':d.get('sim_time_ns'),'session':d.get('session'),'epoch':d.get('epoch'),
                'probe_phase':current['probe_phase'],'goal_request_id':request.get('request_id'),
                'action_goal_id':request.get('action_goal_id')}
    def requested_goal_context():
        request=current.get('goal_request')
        return None if request is None else {
            'request_id':request['request_id'],'action':request['action'],
            'requested_poses':request['requested_poses'],'action_goal_id':request.get('action_goal_id'),
            'latest_feedback_waypoint_index':current['feedback'][-1]
                if request['action']=='follow_waypoints' and current['feedback'] else None}
    def begin_goal_request(action,poses):
        request={**context(),'request_id':len(goal_requests)+1,'goal_request_id':len(goal_requests)+1,'action':action,
                 'requested_poses':[stamped_pose_record(p.header,p.pose) for p in poses],
                 'action_goal_id':None}
        goal_requests.append(request);current['goal_request']=request
    def record_goal_response(handle):
        request=current.get('goal_request')
        if request is None:return
        goal_id=getattr(handle,'goal_id',None)
        request['action_goal_id']=bytes(goal_id.uuid).hex() if goal_id is not None else None
        goal_events.append({**context(),'event':'goal_response','accepted':bool(handle.accepted),
                            'action_goal_id':request['action_goal_id']})
    def waypoint_feedback(message):
        current['feedback'].append(message.feedback.current_waypoint)
        goal_events.append({**context(),'event':'waypoint_feedback',
                            'current_waypoint':int(message.feedback.current_waypoint)})
    def plan(message):
        current['path_points']=max(current['path_points'],len(message.poses))
        row={**context(),**header_record(message.header),'pose_count':len(message.poses),
             'first_pose':stamped_pose_record(message.poses[0].header,message.poses[0].pose) if message.poses else None,
             'last_pose':stamped_pose_record(message.poses[-1].header,message.poses[-1].pose) if message.poses else None,
             'requested_goal':requested_goal_context()}
        plan_samples.append(row);current['latest_plan']=row
    def transforms(message,topic):
        for transform in message.transforms:
            parent=transform.header.frame_id.lstrip('/');child=transform.child_frame_id.lstrip('/')
            if not (parent.startswith('aim_sentry/') or child.startswith('aim_sentry/')
                    or (parent in {'map','odom','base_link'} and child in
                        {'odom','base_link','navigation_frame','base_stabilized'})):
                continue
            t,q=transform.transform.translation,transform.transform.rotation
            row={**context(),**header_record(transform.header),'child_frame_id':transform.child_frame_id,
                 'translation_m':[t.x,t.y,t.z],'rotation_xyzw':[q.x,q.y,q.z,q.w],
                 'source_topic':topic}
            selected_transforms.append(row);latest_transforms[(parent,child)]=row
    def obstacle_scan(message):
        obstacle_scans.append({**context(),**header_record(message.header),
            'angle_min':scan_number(message.angle_min),'angle_max':scan_number(message.angle_max),
            'angle_increment':scan_number(message.angle_increment),
            'time_increment':scan_number(message.time_increment),'scan_time':scan_number(message.scan_time),
            'range_min':scan_number(message.range_min),'range_max':scan_number(message.range_max),
            'ranges':[scan_number(value) for value in message.ranges],
            'intensities':[scan_number(value) for value in message.intensities],
            'nonfinite_encoding':{'nan':'nan','positive_infinity':'inf','negative_infinity':'-inf'},
            'requested_goal':requested_goal_context()})
    def record_precision(mode,status,error,missed_waypoints):
        request=current.get('goal_request') or {}
        goal_events.append({**context(),'event':'action_result_observed_after_original_0p2s_pump',
                            'action_status':int(status),'missed_waypoints':missed_waypoints})
        precision_samples.append({**context(),'mode':mode,'action_status':int(status),
            'missed_waypoints':missed_waypoints,'actual_error_m':error,
            'requested_goal':requested_goal_context(),
            'truth_pose':current.get('truth_pose'),'amcl_pose':current.get('amcl_detail'),
            'odometry_pose':current.get('odom_detail'),'latest_plan':current.get('latest_plan'),
            'latest_transforms':list(latest_transforms.values())})
        if localization_clouds is not None:
            precision_samples[-1]['localization_cloud_snapshot_index'] = localization_clouds.snapshot(
                mode, context(), (current.get('amcl_detail') or {}).get('stamp_ns'),
                obstacle_scans[-1].get('stamp_ns') if obstacle_scans else None)
    def pose(m):
        v=m.pose.position;current['truth']=[v.x,v.y,v.z]
        timestamp=m.header.stamp.sec+m.header.stamp.nanosec*1e-9
        current['truth_stamp']=timestamp
        raw_truth={**context(),**stamped_pose_record(m.header,m.pose),
                   'source_topic':'/rmsim/evaluation/aim_sentry/ground_truth'}
        truth_poses.append(raw_truth);current['truth_pose']=raw_truth
        if timestamp-last_sample[0]>=.05:
            previous=motion_samples[-1] if motion_samples else None
            velocity=vector_derivative(previous['stamp'] if previous else None,
                previous['position_m'] if previous else None,timestamp,[v.x,v.y,v.z])
            acceleration=vector_derivative(previous['stamp'] if previous else None,
                previous['velocity_m_s'] if previous else None,timestamp,velocity) if velocity is not None else None
            q=m.pose.orientation
            motion={'stamp':timestamp,'received_monotonic_s':time.monotonic(),
                    'frame_id':m.header.frame_id,'position_m':[v.x,v.y,v.z],
                    'orientation_xyzw':[q.x,q.y,q.z,q.w],
                    'velocity_m_s':velocity,'acceleration_m_s2':acceleration,
                    'speed_xy_m_s':math.hypot(*velocity[:2]) if velocity is not None else None,
                    'probe_phase':current['probe_phase']}
            motion_samples.append(motion);current['latest_motion']=motion
            trajectory.append([timestamp,v.x,v.y,v.z]);last_sample[0]=timestamp
            if current['amcl']:
                p=current['amcl'];estimates.append({'stamp':timestamp,'actual':[v.x,v.y,v.z], 'amcl':p[:3], 'amcl_stamp':p[3], 'error_xy':math.dist((v.x,v.y),p[:2]), 'odometry':current['odom'],
                    'actual_orientation_xyzw':raw_truth['orientation_xyzw'],
                    'amcl_pose':current['amcl_detail'],'odometry_pose':current['odom_detail']})
    def amcl_pose(m):
        v=m.pose.pose.position
        current['amcl']=[v.x,v.y,v.z,m.header.stamp.sec+m.header.stamp.nanosec*1e-9]
        row={**context(),**stamped_pose_record(m.header,m.pose.pose),'covariance':list(m.pose.covariance)}
        amcl_poses.append(row);current['amcl_detail']=row
    def odom_pose(m):
        v=m.pose.pose.position
        current['odom']=[v.x,v.y,v.z,m.header.stamp.sec+m.header.stamp.nanosec*1e-9]
        twist=m.twist.twist
        row={**context(),**stamped_pose_record(m.header,m.pose.pose),
             'child_frame_id':m.child_frame_id,'pose_covariance':list(m.pose.covariance),
             'linear_velocity_m_s':[twist.linear.x,twist.linear.y,twist.linear.z],
             'angular_velocity_rad_s':[twist.angular.x,twist.angular.y,twist.angular.z]}
        odometry_poses.append(row);current['odom_detail']=row
    def record_command(stage,m,stamped=False):
        twist=m.twist if stamped else m
        source_stamp=m.header.stamp.sec+m.header.stamp.nanosec*1e-9 if stamped else None
        clock_stamp=current['clock_ns']*1e-9 if current['clock_ns'] else None
        stamp=source_stamp if stamped else clock_stamp
        vector=[twist.linear.x,twist.linear.y,twist.angular.z]
        previous=command_streams[stage][-1] if command_streams[stage] else None
        derivative=vector_derivative(previous['derivative_stamp_s'] if previous else None,
            previous['velocity_x_y_yaw'] if previous else None,stamp,vector)
        command_streams[stage].append({'received_monotonic_s':time.monotonic(),
            'source_stamp_s':source_stamp,'latest_clock_stamp_s':clock_stamp,
            'derivative_stamp_s':stamp,
            'stamp_basis':'message_header' if stamped else 'latest_received_clock_unstamped_topic',
            'frame_id':m.header.frame_id if stamped else None,
            'velocity_x_y_yaw':vector,'velocity_units':['m/s','m/s','rad/s'],
            'derivative_x_y_yaw':derivative,'derivative_units':['m/s2','m/s2','rad/s2'],
            'probe_phase':current['probe_phase']})
    def on_command(m):
        commands.append([m.header.stamp.sec+m.header.stamp.nanosec*1e-9, m.twist.linear.x, m.twist.linear.y, m.twist.angular.z])
        record_command('vehicle',m,True)
    def diag(m):
        d=json.loads(m.data);current['diag']=d
        received=time.monotonic()
        performance_trace.append({'received_monotonic_s':received,
            'receipt_interval_s':received-performance_trace[-1]['received_monotonic_s'] if performance_trace else None,
            'sim_time_ns':d.get('sim_time_ns'),'session':d.get('session'),'epoch':d.get('epoch'),
            'connected':d.get('connected'),'state_fresh':d.get('state_fresh'),
            'clock_source':d.get('clock_source'),'fixed_step_s':d.get('fixed_step_s'),
            'simulation_rate':d.get('simulation_rate'),'rendering':d.get('rendering'),
            'drive':d.get('drive'),'command':d.get('command'),
            'forwarded_commands':d.get('forwarded_commands'),'rejected_commands':d.get('rejected_commands'),
            'command_error':d.get('command_error'),'transport_error':d.get('transport_error'),
            'probe_phase':current['probe_phase']})
        if d.get('imu') and d.get('lidar'):
            sensor_status.append({'stamp':d['sim_time_ns']*1e-9,'imu':d['imu'].get('stats'),'lidar':{k:d['lidar'].get(k) for k in ('traced_rays','returns','dropped_scans','published_scans','history_resets')},'bridge':{k:d.get(k) for k in ('sim_time_ns','fixed_step_s','simulation_rate')}})
        a=d.get('aim',{});f=d.get('aim_adapter',{}).get('navigation_follow',{})
        if a:
            joint_yaws.append(a['yaw_rad'])
            if a.get('trigger_active'):report['unexpected_fire']=True
        if d.get('aim_adapter',{}).get('source')=='navigation' and 'yaw' in f:
            following.append({'actual_yaw':a['yaw_rad'],'target_yaw':f['yaw'],'rate':f['yaw_rate'],'stamp':d['sim_time_ns']})
    def cloud(m):
        if m.header.frame_id=='aim_sentry/lidar_link' and m.width>100:
            current['clouds']+=1
            cloud_stamps.append([m.header.stamp.sec+m.header.stamp.nanosec*1e-9,int(m.width)])
    def imu(m):
        if m.header.frame_id=='aim_sentry/mid360_imu_link':
            current['imu']+=1
            a=m.linear_acceleration;g=m.angular_velocity
            imu_samples.append([m.header.stamp.sec+m.header.stamp.nanosec*1e-9,a.x,a.y,a.z,g.x,g.y,g.z])
    node.create_subscription(PoseStamped,'/rmsim/evaluation/aim_sentry/ground_truth',pose,qos_profile_sensor_data)
    node.create_subscription(PoseWithCovarianceStamped,'/amcl_pose',amcl_pose,1)
    node.create_subscription(Odometry,'/odometry',odom_pose,qos_profile_sensor_data)
    node.create_subscription(TwistStamped,'/aim_sentry/cmd_vel',on_command,1)
    node.create_subscription(Twist,'/cmd_vel_controller',lambda m:record_command('controller',m),10)
    node.create_subscription(Twist,'/cmd_vel_nav2_result',lambda m:record_command('smoothed',m),10)
    node.create_subscription(Clock,'/clock',lambda m:current.update(clock_ns=m.clock.sec*10**9+m.clock.nanosec),1)
    node.create_subscription(String,'/rmsim/aim_sentry/navigation',lambda m:current.update(nav=json.loads(m.data)),1)
    node.create_subscription(String,'/rmsim/aim_sentry/state',diag,1)
    node.create_subscription(NavPath,'/plan',plan,1)
    node.create_subscription(LaserScan,'/obstacle_scan',obstacle_scan,qos_profile_sensor_data)
    node.create_subscription(TFMessage,'/tf',lambda m:transforms(m,'/tf'),qos_profile_sensor_data)
    static_tf_qos=QoSProfile(depth=10,reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
    node.create_subscription(TFMessage,'/tf_static',lambda m:transforms(m,'/tf_static'),static_tf_qos)
    node.create_subscription(PointCloud2,'/aim_sentry/lidar/points',cloud,qos_profile_sensor_data)
    if localization_clouds is not None:
        capture_qos=QoSProfile(depth=1,reliability=ReliabilityPolicy.BEST_EFFORT)
        for capture_topic in TOPICS:
            node.create_subscription(PointCloud2,capture_topic,
                lambda m,t=capture_topic:localization_clouds.add(t,m,context()),capture_qos)
    node.create_subscription(Imu,'/aim_sentry/mid360_imu/data_raw',imu,qos_profile_sensor_data)
    single=ActionClient(node,NavigateToPose,'navigate_to_pose');multi=ActionClient(node,FollowWaypoints,'follow_waypoints');clients.extend([single,multi])
    def cancel_event(name,**details):
        d=current['diag'] or {}
        item={'event':name,'monotonic_s':time.monotonic(),'truth_stamp_s':current['truth_stamp'],
              'bridge_sim_time_ns':d.get('sim_time_ns'),'latest_clock_ns':current['clock_ns'],
              'position_m':current['truth'][: ] if current['truth'] is not None else None,
              'motion':current['latest_motion'],'drive':d.get('drive'),'command':d.get('command'),**details}
        cancel_events.append(item)
        return item
    def save():
        report['last_navigation']=current['nav'];report['last_position']=current['truth']
        (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    def check(name,passed,**details):
        report['checks'].append(dict(name=name,passed=bool(passed),**details));save()
        if not passed:raise AssertionError(name)
        print('PASS',name,flush=True)
    def wait(condition,timeout=180):
        end=time.monotonic()+timeout
        while not condition():
            if time.monotonic()>end:raise TimeoutError('Sentry navigation deadline exceeded')
            rclpy.spin_once(node,timeout_sec=.01)
    def pump(seconds):
        end=time.monotonic()+seconds
        while time.monotonic()<end:rclpy.spin_once(node,timeout_sec=.01)
    def goal_pose(x,y):
        m=PoseStamped();m.header.frame_id='aim_sentry/map';m.pose.position.x=float(x);m.pose.position.y=float(y);m.pose.orientation.w=1.
        return m
    try:
        wait(lambda:current['nav'] and current['nav']['phase']=='running' and current['truth'] and current['diag'] and single.server_is_ready())
        identity=list(current['nav']['identity']);check('native_stack_ready',current['diag']['state_fresh'],identity=identity)
        current['probe_phase']='single_goal'
        goal=NavigateToPose.Goal();goal.pose=goal_pose(9.45,6.4)
        begin_goal_request('navigate_to_pose',[goal.pose])
        future=single.send_goal_async(goal);wait(future.done,30);active=future.result();record_goal_response(active);check('single_goal_accepted',active.accepted)
        result=active.get_result_async();wait(result.done,360);pump(.2)
        error=math.dist(current['truth'][:2],[9.45,6.4]);status=result.result().status
        record_precision('single_goal',status,error,[])
        check('single_goal_actual_arrival',status==4 and error<.25,status=status,error_m=error,position=current['truth'])
        check('original_planner_path',current['path_points']>2,points=current['path_points'])
        wait(multi.server_is_ready,30);waypoints=FollowWaypoints.Goal();waypoints.poses=[goal_pose(5.8,6.4),goal_pose(9.45,6.4)]
        current['probe_phase']='waypoints'
        current['feedback']=[]
        begin_goal_request('follow_waypoints',waypoints.poses)
        future=multi.send_goal_async(waypoints,feedback_callback=waypoint_feedback)
        wait(future.done,30);active=future.result();record_goal_response(active);check('waypoint_sequence_accepted',active.accepted)
        result=active.get_result_async();wait(result.done,600);pump(.2)
        outcome=result.result();error=math.dist(current['truth'][:2],[9.45,6.4])
        record_precision('waypoints',outcome.status,error,list(outcome.result.missed_waypoints))
        check('waypoint_sequence_actual_arrival',outcome.status==4 and not outcome.result.missed_waypoints and error<.25,status=outcome.status,missed_waypoints=list(outcome.result.missed_waypoints),error_m=error)
        feedback=current['feedback'];check('waypoints_observed_in_order',0 in feedback and 1 in feedback and feedback==sorted(feedback),indices=sorted(set(feedback)))
        current['probe_phase']='cancel_test'
        goal.pose=goal_pose(5.8,6.4);begin_goal_request('navigate_to_pose',[goal.pose]);future=single.send_goal_async(goal);wait(future.done,30);active=future.result();record_goal_response(active);check('cancel_test_goal_accepted',active.accepted)
        origin=current['truth'][:];wait(lambda:math.dist(origin,current['truth'])>.15,90)
        before=current['truth'][:];report['cancel_request']=cancel_event('request')
        future=active.cancel_goal_async();wait(future.done,20)
        cancel_event('response',return_code=int(future.result().return_code))
        result=active.get_result_async();wait(result.done,30)
        cancel_event('action_result',status=int(result.result().status))
        check('native_action_cancelled',result.result().status==5,status=result.result().status)
        current['probe_phase']='cancel_settle'
        pump(4);stopped=current['truth'][:];cancel_event('stop_observation')
        pump(2);drift=math.dist(stopped,current['truth']);cancel_event('drift_observation')
        check('cancelled_vehicle_stopped',math.dist(before,stopped)<.2 and drift<.02,braking_m=math.dist(before,stopped),drift_m=drift)
        errors=[abs(math.atan2(math.sin(r['actual_yaw']-r['target_yaw']),math.cos(r['actual_yaw']-r['target_yaw']))) for r in following]
        travel=sum(abs(math.atan2(math.sin(b-a),math.cos(b-a))) for a,b in zip(joint_yaws,joint_yaws[1:]))
        check('gimbal_follows_original_shaped_target',len(errors)>50 and statistics.median(errors)<.08 and travel>.3,samples=len(errors),median_error_rad=statistics.median(errors) if errors else None,yaw_travel_rad=travel)
        check('original_gimbal_rate_limit',bool(following) and max(abs(r['rate']) for r in following)<=1.000001)
        check('mounted_lidar_and_imu_live',current['clouds']>10 and current['imu']>200,clouds=current['clouds'],imu=current['imu'])
        check('session_and_no_fire_preserved',current['nav']['identity']==identity and not report.get('unexpected_fire'))
        report['status']='passed'
    except Exception as error:
        report.update(status='failed',error=repr(error));print('SENTRY_GATE_FAILED',repr(error),flush=True)
    finally:
        if active is not None and active.accepted:
            try:
                cancel=active.cancel_goal_async();wait(cancel.done,5)
            except Exception:pass
        report['runtime_provenance_sha256']=provenance_sha256
        save()
        trace_metadata={'schema_version':2,'scope':'Read-only raw observations; no truth enters localization or control',
            'topics':{'truth_poses':'/rmsim/evaluation/aim_sentry/ground_truth','amcl_poses':'/amcl_pose',
                      'odometry_poses':'/odometry','plan_samples':'/plan','obstacle_scans':'/obstacle_scan',
                      'selected_transforms':['/tf','/tf_static']},
            'runtime_provenance_path':str(provenance_path) if provenance else None,
            'runtime_provenance_sha256':provenance_sha256,
            'source_control_config_sha256':provenance.get('source_control_config_sha256'),
            'module_sha256':provenance.get('module_sha256'),'engine_config_sha256':provenance.get('engine_config_sha256'),
            'scene':provenance.get('scene'),'script_sha256':report['script_sha256'],
            'scan_sampling':'Every received /obstacle_scan message; no resampling or synthesized ranges',
            'transform_sampling':'Raw /tf and latched /tf_static involving aim_sentry, preserving publisher stamps',
            'terrain_map_ext':'Optional raw capture enabled' if localization_clouds else 'Not subscribed in this observer; raw LaserScan/TF/poses support offline map alignment',
            'localization_cloud_capture':{'enabled':bool(localization_clouds),'topics':list(TOPICS) if localization_clouds else [],
                'manifest_path':str(args.output/'localization-clouds/verification.json') if localization_clouds else None,
                'scope':'Bounded optional raw binary capture; disabled for normal acceptance'}}
        trace_data={'trajectory':trajectory,'following':following,'estimates':estimates,'commands':commands,
            'imu_samples':imu_samples,'sensor_status':sensor_status,'cloud_stamps':cloud_stamps,
            'performance':performance_trace,'command_streams':command_streams,'motion':motion_samples,
            'cancel_events':cancel_events,'metadata':trace_metadata,'goal_requests':goal_requests,
            'goal_events':goal_events,'plan_samples':plan_samples,'truth_poses':truth_poses,
            'amcl_poses':amcl_poses,'odometry_poses':odometry_poses,'selected_transforms':selected_transforms,
            'obstacle_scans':obstacle_scans,'precision_endpoints':precision_samples}
        trace_path=args.output/'trace.json'
        trace_path.write_text(json.dumps(trace_data),encoding='utf-8')
        if localization_clouds is not None:
            try:
                report['localization_cloud_capture']=localization_clouds.write(args.output/'localization-clouds',
                    {'runtime_provenance_path':str(provenance_path),'runtime_provenance_sha256':provenance_sha256,
                     'trace_path':str(trace_path),'trace_sha256':hashlib.sha256(trace_path.read_bytes()).hexdigest(),
                     'probe_script_sha256':report['script_sha256'],'module_sha256':provenance.get('module_sha256'),
                     'source_control_config_sha256':provenance.get('source_control_config_sha256'),
                     'scene':provenance.get('scene'),'localization_map':provenance.get('localization_map')})
            except Exception as capture_error:
                report['localization_cloud_capture'].update(status='failed',error=repr(capture_error),capture=localization_clouds.summary())
            save()
        identity=list(current['nav']['identity']) if current['nav'] and current['nav'].get('identity') else None
        precision=precision_diagnostic(precision_samples,report['status'],identity,provenance_sha256,
                                       hashlib.sha256(trace_path.read_bytes()).hexdigest())
        precision.update(script_sha256=report['script_sha256'],trace_path=str(trace_path),
                         runtime_provenance_path=str(provenance_path) if provenance else None,
                         source_control_config_sha256=provenance.get('source_control_config_sha256'))
        precision_directory=args.output/'precision';precision_directory.mkdir(exist_ok=False)
        (precision_directory/'verification.json').write_text(json.dumps(precision,indent=2)+'\n',encoding='utf-8')
        for client in clients:client.destroy()
        node.destroy_node();rclpy.shutdown()
    print('SENTRY_NAVIGATION',report['status'],len(report['checks']),flush=True)
    return 0 if report['status']=='passed' else 2

if __name__=='__main__':raise SystemExit(main())
