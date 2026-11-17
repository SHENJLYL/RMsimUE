"""Send an explicit position goal to the original Nav2 action server."""
import argparse,json,math,time
import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from std_msgs.msg import String
from nav2_msgs.action import NavigateToPose

def main():
    p=argparse.ArgumentParser();p.add_argument('x',type=float);p.add_argument('y',type=float);p.add_argument('--robot',default='aim_sentry');p.add_argument('--timeout',type=float,default=300);args=p.parse_args()
    if not all(math.isfinite(v) for v in (args.x,args.y,args.timeout)) or args.timeout<=0:raise ValueError('Goal and timeout must be finite')
    rclpy.init();node=Node('rmsim_send_navigation_goal');state={};deadline=time.monotonic()+args.timeout
    node.create_subscription(String,'/rmsim/'+args.robot+'/navigation',lambda m:state.update(json.loads(m.data)),1)
    client=ActionClient(node,NavigateToPose,'navigate_to_pose');handle=None
    def wait(predicate):
        while not predicate():
            if time.monotonic()>deadline:raise TimeoutError('Navigation goal deadline exceeded')
            rclpy.spin_once(node,timeout_sec=.05)
    try:
        wait(lambda:state.get('phase')=='running' and client.server_is_ready())
        identity=state['identity'];goal=NavigateToPose.Goal();goal.pose.header.frame_id=args.robot+'/map';goal.pose.pose.position.x=args.x;goal.pose.pose.position.y=args.y;goal.pose.pose.orientation.w=1.
        # Zero stamp requests the latest valid transform; no wall-clock timestamp is injected.
        future=client.send_goal_async(goal);wait(future.done);handle=future.result()
        if not handle.accepted:raise RuntimeError('Original Nav2 rejected the goal')
        result=handle.get_result_async();print('Goal accepted; waiting for actual Nav2 result.',flush=True)
        wait(lambda:result.done() or state.get('identity')!=identity)
        if state.get('identity')!=identity:raise RuntimeError('Simulator session changed; goal invalidated')
        status=result.result().status
        print('Nav2 goal status:',status)
        if status!=4:raise RuntimeError('Goal did not succeed')
    finally:
        if handle and handle.accepted:
            cancel=handle.cancel_goal_async();end=time.monotonic()+2
            while not cancel.done() and time.monotonic()<end:rclpy.spin_once(node,timeout_sec=.05)
        node.destroy_node();rclpy.shutdown()

if __name__=='__main__':main()
