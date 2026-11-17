"""Small real ROS publisher using the simulator clock, with a bounded run time."""
import json
import math
import time
import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from geometry_msgs.msg import TwistStamped
from std_msgs.msg import String
from .protocol import identifier


def main(args=None):
    rclpy.init(args=args)
    node=Node('rmsim_drive_demo',parameter_overrides=[Parameter('use_sim_time',value=True)])
    robot=identifier(node.declare_parameter('robot_id','aim_sentry').value)
    backend=node.declare_parameter('control_backend','velocity').value
    if backend not in ('velocity','lk'):raise ValueError('control_backend must be velocity or lk')
    if backend=='lk':
        from .control_dlls import load_control_dlls
        load_control_dlls()
        from chassis_controllers.msg import ChassisControl
    values=[float(node.declare_parameter(k,v).value) for k,v in [('vx',.3),('vy',0.),('wz',0.),('duration_s',3.)]]
    if not all(math.isfinite(v) for v in values) or not 0<values[3]<=30:
        node.destroy_node();rclpy.shutdown();raise ValueError('finite velocities and duration_s in (0,30] required')
    status={};received=0.
    def diagnostic(message):
        nonlocal status,received
        status=json.loads(message.data);received=time.monotonic()
    node.create_subscription(String,'/rmsim/'+robot+'/state',diagnostic,1)
    publisher=node.create_publisher(ChassisControl,'/rmsim/'+robot+'/chassis_command',1) if backend=='lk' else node.create_publisher(TwistStamped,'/'+robot+'/cmd_vel',1)
    def send(vx,vy,wz):
        if backend=='lk':
            message=ChassisControl();message.x_speed=vx;message.y_speed=vy;message.spin_speed=wz
            message.power_limit=100;publisher.publish(message);return
        message=TwistStamped();message.header.frame_id=robot+'/base_link';message.header.stamp=node.get_clock().now().to_msg()
        message.twist.linear.x=vx;message.twist.linear.y=vy;message.twist.angular.z=wz;publisher.publish(message)
    try:
        deadline=time.monotonic()+10
        while not(status.get('connected') and status.get('state_fresh') and node.get_clock().now().nanoseconds>0):
            if time.monotonic()>deadline:raise RuntimeError('no fresh simulator bridge/clock')
            rclpy.spin_once(node,timeout_sec=.05)
        if 'manual_mode' in status.get('command',{}).get('status',''):
            raise RuntimeError('UE is observation-only; start it with -RosControl for this demo')
        deadline=time.monotonic()+values[3];next_send=0.
        while time.monotonic()<deadline:
            rclpy.spin_once(node,timeout_sec=.01)
            if time.monotonic()-received>1 or not status.get('state_fresh'):raise RuntimeError('bridge state lost; stopping publisher')
            if time.monotonic()>=next_send:send(*values[:3]);next_send=time.monotonic()+.05
    except KeyboardInterrupt:
        pass
    finally:
        if rclpy.ok():
            end=time.monotonic()+.25
            while time.monotonic()<end:
                send(0.,0.,0.);rclpy.spin_once(node,timeout_sec=.02)
        node.destroy_node()
        if rclpy.ok():rclpy.shutdown()


if __name__=='__main__':main()
