"""Bounded publisher of the original sentry aim/scan messages; no detection algorithm."""
import json
import math
import time
import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from std_msgs.msg import Int32,String
from .control_dlls import load_package_dlls
from .protocol import identifier


def main(args=None):
    rclpy.init(args=args);node=None
    try:
        load_package_dlls('sp_msgs','pb_rm_interfaces')
        from sp_msgs.msg import AutoAimCommandMsg
        from pb_rm_interfaces.msg import GimbalCmd
        node=Node('rmsim_aim_demo',parameter_overrides=[Parameter('use_sim_time',value=True)])
        robot=identifier(node.declare_parameter('robot_id','aim_sentry').value)
        mode=node.declare_parameter('mode','scan').value
        duration=float(node.declare_parameter('duration_s',5.).value)
        yaw=float(node.declare_parameter('yaw_rad',.4).value);pitch=float(node.declare_parameter('pitch_rad',-.1).value)
        shoot=node.declare_parameter('shoot',False).value
        if mode not in ('scan','aim') or not all(math.isfinite(x) for x in (duration,yaw,pitch)) or not 0<duration<=30 or abs(pitch)>math.pi/2:
            raise ValueError('mode scan/aim, duration_s in(0,30], and finite world angles required')
        prefix='/'+robot;status={};received=0.
        def on_state(message):
            nonlocal status,received
            status=json.loads(message.data);received=time.monotonic()
        node.create_subscription(String,'/rmsim'+prefix+'/state',on_state,1)
        auto_pub=node.create_publisher(AutoAimCommandMsg,prefix+'/autoaim_command',1)
        scan_pub=node.create_publisher(GimbalCmd,prefix+'/gimbal_scan_cmd',1)
        switch=node.create_publisher(Int32,prefix+'/auto_aim_switch',1)
        def emit(stopping=False):
            switch.publish(Int32(data=int(mode=='aim' and not stopping)))
            if mode=='aim':
                m=AutoAimCommandMsg();m.timestamp=node.get_clock().now().to_msg();m.control=not stopping
                m.shoot=bool(shoot and not stopping);m.yaw=yaw;m.pitch=pitch;auto_pub.publish(m)
            else:
                m=GimbalCmd();m.header.stamp=node.get_clock().now().to_msg();m.yaw_type=2;m.pitch_type=2
                m.velocity.yaw=0. if stopping else .5;m.velocity.pitch=0.
                m.velocity.yaw_min_range=-.5;m.velocity.yaw_max_range=.5;m.velocity.pitch_min_range=-.1;m.velocity.pitch_max_range=.1
                scan_pub.publish(m)
        deadline=time.monotonic()+10
        while not(status.get('connected') and status.get('state_fresh') and status.get('aim',{}).get('enabled') and 'aim_adapter' in status and node.get_clock().now().nanoseconds>0):
            if time.monotonic()>deadline:raise RuntimeError('need UE RosAimControl and bridge aim_control with fresh simulator clock')
            rclpy.spin_once(node,timeout_sec=.02)
        try:
            end=time.monotonic()+duration;next_send=0.
            while time.monotonic()<end:
                rclpy.spin_once(node,timeout_sec=.01)
                if time.monotonic()-received>.25 or not status.get('state_fresh'):raise RuntimeError('aim bridge state lost')
                if time.monotonic()>=next_send:emit();next_send=time.monotonic()+.03
        finally:
            end=time.monotonic()+.25
            while rclpy.ok() and time.monotonic()<end:emit(True);rclpy.spin_once(node,timeout_sec=.02)
        print('Completed',mode,'demo for',duration,'wall seconds')
    except KeyboardInterrupt:pass
    finally:
        if node is not None:node.destroy_node()
        if rclpy.ok():rclpy.shutdown()


if __name__=='__main__':main()
