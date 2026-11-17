"""Independent observer for goals actually sent through the RViz UI; sends no commands."""
import argparse,json,math,os,time
from pathlib import Path
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile,DurabilityPolicy,ReliabilityPolicy,qos_profile_sensor_data
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Path as NavPath
from std_msgs.msg import String
from action_msgs.msg import GoalStatusArray
from nav2_msgs.action import FollowWaypoints


def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['single','waypoints','cancel'],required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--domain',type=int,default=71)
    p.add_argument('--x',type=float,default=9.45);p.add_argument('--y',type=float,default=6.4);args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    os.environ['ROS_DOMAIN_ID']=str(args.domain);os.environ['ROS_LOCALHOST_ONLY']='1'
    rclpy.init();node=Node('rmsim_console_acceptance_observer')
    current={'pose':None,'nav':None,'statuses':[],'feedback':[],'path_points':0};trace=[]
    action='follow_waypoints' if args.mode=='waypoints' else 'navigate_to_pose'
    def pose(m):
        current['pose']=[m.pose.position.x,m.pose.position.y,m.pose.position.z]
        trace.append([m.header.stamp.sec+m.header.stamp.nanosec*1e-9,*current['pose']])
    node.create_subscription(PoseStamped,'/rmsim/evaluation/aim_sentry/ground_truth',pose,qos_profile_sensor_data)
    node.create_subscription(String,'/rmsim/aim_sentry/navigation',lambda m:current.update(nav=json.loads(m.data)),1)
    latched=QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL)
    node.create_subscription(GoalStatusArray,'/'+action+'/_action/status',lambda m:current.update(statuses=[(bytes(s.goal_info.goal_id.uuid).hex(),s.status) for s in m.status_list]),latched)
    node.create_subscription(NavPath,'/plan',lambda m:current.update(path_points=max(current['path_points'],len(m.poses))),1)
    if args.mode=='waypoints':
        node.create_subscription(FollowWaypoints.Impl.FeedbackMessage,'/follow_waypoints/_action/feedback',lambda m:current['feedback'].append(m.feedback.current_waypoint),10)
    report={'status':'running','mode':args.mode,'checks':[],'scope':'actual RViz GUI goals; independent truth evaluation; no control/pose injection'}
    def save():
        (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    def check(name,passed,**details):
        report['checks'].append(dict(name=name,passed=bool(passed),**details));save()
        if not passed:raise AssertionError(name)
        print('PASS',name,flush=True)
    def wait(condition,timeout=120):
        end=time.monotonic()+timeout
        while not condition():
            if time.monotonic()>end:raise TimeoutError('GUI acceptance observation timeout')
            rclpy.spin_once(node,timeout_sec=.02)
    try:
        wait(lambda:current['pose'] and current['nav'] and current['nav']['phase']=='running')
        # Let historical transient-local action states arrive before marking the boundary.
        until=time.monotonic()+1
        while time.monotonic()<until:rclpy.spin_once(node,timeout_sec=.02)
        old={key for key,status in current['statuses']};start=list(current['pose'])
        (args.output/'ready-for-ui.json').write_text(json.dumps({'mode':args.mode,'start':start,'old_goals':sorted(old)}),encoding='utf-8')
        print('READY_FOR_UI',args.mode,flush=True)
        wait(lambda:any(key not in old and status in (1,2,4) for key,status in current['statuses']),120)
        goal=next(key for key,status in current['statuses'] if key not in old)
        check('rviz_action_reaches_original_nav2',True,goal_id=goal,action=action)
        if args.mode=='cancel':
            wait(lambda:math.dist(start,current['pose'])>.15,60)
            before=list(current['pose'])
            (args.output/'ready-to-cancel.json').write_text(json.dumps({'before':before,'goal_id':goal}),encoding='utf-8')
            print('READY_TO_CANCEL',flush=True)
            wait(lambda:any(key==goal and status==5 for key,status in current['statuses']),90)
            check('rviz_cancel_returns_cancelled',True)
            end=time.monotonic()+3
            while time.monotonic()<end:rclpy.spin_once(node,timeout_sec=.02)
            stopped=list(current['pose']);end=time.monotonic()+2
            while time.monotonic()<end:rclpy.spin_once(node,timeout_sec=.02)
            # The marker-to-click distance includes remote UI operator delay. Measure settling independently.
            check('cancelled_vehicle_stays_stopped',math.dist(stopped,current['pose'])<.02,drift_m=math.dist(stopped,current['pose']))
        else:
            wait(lambda:any(key==goal and status in (4,5,6) for key,status in current['statuses']),300)
            status=next(status for key,status in current['statuses'] if key==goal)
            check('original_nav2_goal_success',status==4,status=status)
            error=math.hypot(current['pose'][0]-args.x,current['pose'][1]-args.y)
            check('actual_vehicle_reaches_selected_point',error<.25,error_m=error,position=current['pose'],target=[args.x,args.y])
            check('real_planner_path_visible',current['path_points']>2,path_points=current['path_points'])
            if args.mode=='waypoints':
                check('both_waypoints_executed_in_order',0 in current['feedback'] and 1 in current['feedback'] and current['feedback']==sorted(current['feedback']),waypoints=sorted(set(current['feedback'])))
                check('physical_outbound_leg_observed',max(row[1] for row in trace)>args.x-.30 and min(row[1] for row in trace)<6.1,maximum_x=max(row[1] for row in trace),minimum_x=min(row[1] for row in trace),requested_extent=[5.8,args.x])
        report['status']='passed'
    except Exception as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        save();(args.output/'trajectory.json').write_text(json.dumps(trace),encoding='utf-8');node.destroy_node();rclpy.shutdown()

if __name__=='__main__':main()
