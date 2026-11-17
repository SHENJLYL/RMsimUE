"""Native sentry-console action/route gate; truth is an independent observer only."""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import statistics
import time


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--domain',type=int,default=71)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    os.environ['ROS_DOMAIN_ID']=str(args.domain)
    from rmsim_bridge.native_transport import configure_navigation_transport
    configure_navigation_transport()
    import rclpy
    from rclpy.node import Node
    from rclpy.action import ActionClient
    from rclpy.qos import qos_profile_sensor_data
    from geometry_msgs.msg import PoseStamped
    from nav_msgs.msg import Path as NavPath
    from nav2_msgs.action import NavigateToPose,FollowWaypoints
    from sensor_msgs.msg import PointCloud2,Imu
    from std_msgs.msg import String
    rclpy.init();node=Node('rmsim_sentry_navigation_acceptance')
    current={'truth':None,'nav':None,'diag':None,'path_points':0,'clouds':0,'imu':0,'feedback':[]}
    trajectory=[];following=[];joint_yaws=[];active=None;clients=[]
    report={'status':'running','scope':'native original Nav2 actions, moving gimbal-mounted Mid-360 and actual joints; truth used only for evaluation','checks':[]}
    def pose(m):
        v=m.pose.position;current['truth']=[v.x,v.y,v.z]
        trajectory.append([m.header.stamp.sec+m.header.stamp.nanosec*1e-9,v.x,v.y,v.z])
    def diag(m):
        d=json.loads(m.data);current['diag']=d
        a=d.get('aim',{});f=d.get('aim_adapter',{}).get('navigation_follow',{})
        if a:
            joint_yaws.append(a['yaw_rad'])
            if a.get('trigger_active'):report['unexpected_fire']=True
        if d.get('aim_adapter',{}).get('source')=='navigation' and 'yaw' in f:
            following.append({'actual_yaw':a['yaw_rad'],'target_yaw':f['yaw'],'rate':f['yaw_rate'],'stamp':d['sim_time_ns']})
    def cloud(m):
        if m.header.frame_id=='aim_sentry/lidar_link' and m.width>100:current['clouds']+=1
    def imu(m):
        if m.header.frame_id=='aim_sentry/mid360_imu_link':current['imu']+=1
    node.create_subscription(PoseStamped,'/rmsim/evaluation/aim_sentry/ground_truth',pose,qos_profile_sensor_data)
    node.create_subscription(String,'/rmsim/aim_sentry/navigation',lambda m:current.update(nav=json.loads(m.data)),1)
    node.create_subscription(String,'/rmsim/aim_sentry/state',diag,1)
    node.create_subscription(NavPath,'/plan',lambda m:current.update(path_points=max(current['path_points'],len(m.poses))),1)
    node.create_subscription(PointCloud2,'/aim_sentry/lidar/points',cloud,qos_profile_sensor_data)
    node.create_subscription(Imu,'/aim_sentry/mid360_imu/data_raw',imu,qos_profile_sensor_data)
    single=ActionClient(node,NavigateToPose,'navigate_to_pose');multi=ActionClient(node,FollowWaypoints,'follow_waypoints');clients.extend([single,multi])
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
        goal=NavigateToPose.Goal();goal.pose=goal_pose(9.45,6.4)
        future=single.send_goal_async(goal);wait(future.done,30);active=future.result();check('single_goal_accepted',active.accepted)
        result=active.get_result_async();wait(result.done,360);pump(.2)
        error=math.dist(current['truth'][:2],[9.45,6.4]);status=result.result().status
        check('single_goal_actual_arrival',status==4 and error<.25,status=status,error_m=error,position=current['truth'])
        check('original_planner_path',current['path_points']>2,points=current['path_points'])
        wait(multi.server_is_ready,30);waypoints=FollowWaypoints.Goal();waypoints.poses=[goal_pose(5.8,6.4),goal_pose(9.45,6.4)]
        current['feedback']=[]
        future=multi.send_goal_async(waypoints,feedback_callback=lambda m:current['feedback'].append(m.feedback.current_waypoint))
        wait(future.done,30);active=future.result();check('waypoint_sequence_accepted',active.accepted)
        result=active.get_result_async();wait(result.done,600);pump(.2)
        outcome=result.result();error=math.dist(current['truth'][:2],[9.45,6.4])
        check('waypoint_sequence_actual_arrival',outcome.status==4 and not outcome.result.missed_waypoints and error<.25,status=outcome.status,missed_waypoints=list(outcome.result.missed_waypoints),error_m=error)
        feedback=current['feedback'];check('waypoints_observed_in_order',0 in feedback and 1 in feedback and feedback==sorted(feedback),indices=sorted(set(feedback)))
        goal.pose=goal_pose(5.8,6.4);future=single.send_goal_async(goal);wait(future.done,30);active=future.result();check('cancel_test_goal_accepted',active.accepted)
        origin=current['truth'][:];wait(lambda:math.dist(origin,current['truth'])>.15,90)
        before=current['truth'][:];future=active.cancel_goal_async();wait(future.done,20);result=active.get_result_async();wait(result.done,30)
        check('native_action_cancelled',result.result().status==5,status=result.result().status)
        pump(4);stopped=current['truth'][:];pump(2);drift=math.dist(stopped,current['truth'])
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
        save();(args.output/'trace.json').write_text(json.dumps({'trajectory':trajectory,'following':following}),encoding='utf-8')
        for client in clients:client.destroy()
        node.destroy_node();rclpy.shutdown()
    print('SENTRY_NAVIGATION',report['status'],len(report['checks']),flush=True)
    return 0 if report['status']=='passed' else 2

if __name__=='__main__':raise SystemExit(main())
