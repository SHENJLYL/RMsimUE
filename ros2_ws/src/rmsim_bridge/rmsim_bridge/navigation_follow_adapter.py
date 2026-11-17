"""Fresh estimated-odometry adapter for pinned sentry navigation yaw following."""
import json
import math
from pathlib import Path
import time
from nav_msgs.msg import Odometry
from rclpy.qos import qos_profile_sensor_data
from .navigation_follow import FollowConfig, NavigationYaw
from .protocol import hold_aim


class NavigationFollowAdapter:
    def __init__(self, node):
        self.node = node
        filename = node.declare_parameter('navigation_follow_profile', '').value
        path = Path(filename)
        if not filename or not path.is_file() or path.stat().st_size > 16384:
            raise ValueError('Navigation follow requires an explicit vehicle-package profile')
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if set(data) != {'schema', 'source_commit', 'control'} or data['schema'] != 'sentry_navigation_follow_v1':
            raise ValueError('Invalid navigation follow profile')
        if data['source_commit'] != '95c66c28717e0b4d0f586c2ebf8df99d47a24ab0':
            raise ValueError('Navigation follow source contract changed')
        self.trajectory = NavigationYaw(FollowConfig(**data['control']))
        self.odom = None
        self.velocity = None
        self.last_time = None
        self.serial = 0
        self.entry = None
        self.report = {'status': 'waiting_for_estimated_odometry'}
        self.subscription = node.create_subscription(Odometry, '/odometry', self.on_odometry, qos_profile_sensor_data)

    def reset(self):
        self.trajectory.reset()
        self.entry = None
        self.velocity = None
        self.odom = None
        self.last_time = None
        self.report = {'status': 'waiting_for_fresh_session_inputs'}

    def on_odometry(self, msg):
        if msg.header.frame_id != self.node.robot_id+'/odom' or msg.child_frame_id != self.node.robot_id+'/base_link':
            return
        q = msg.pose.pose.orientation
        values = [q.x, q.y, q.z, q.w]
        stamp = msg.header.stamp.sec*1_000_000_000+msg.header.stamp.nanosec
        if stamp <= 0 or not all(math.isfinite(v) for v in values) or abs(sum(v*v for v in values)-1) > .01:
            return
        if self.odom and stamp < self.odom['stamp']:
            return
        yaw = math.atan2(2*(q.w*q.z+q.x*q.y), 1-2*(q.y*q.y+q.z*q.z))
        self.odom = {'stamp': stamp, 'yaw': yaw, 'yaw_rate': msg.twist.twist.angular.z}

    def observe_velocity(self, msg):
        # Called only after the normal bridge validates and queues this command.
        self.velocity = {'stamp': msg.header.stamp.sec*1_000_000_000+msg.header.stamp.nanosec,
                         'vx': msg.twist.linear.x, 'vy': msg.twist.linear.y, 'wall': time.monotonic()}

    def poll(self, overridden=False):
        state = self.node.state
        now = state['sim_time_ns']
        previous, self.last_time = self.last_time, now
        dt = (now-previous)*1e-9 if previous is not None else 0.
        if overridden:
            self.trajectory.reset(); self.entry=None; self.report = {'status': 'higher_priority_aim'}; return None
        if not self.odom or not self.velocity:
            return None
        timeout = self.trajectory.config.input_timeout_s
        if not -.05 <= (now-self.odom['stamp'])*1e-9 < timeout or time.monotonic()-self.velocity['wall'] > timeout:
            self.trajectory.reset(); self.entry=None; self.report = {'status': 'stale_navigation_input'}; return None
        if dt==0:return self.entry
        aim = state['aim']
        v = self.velocity
        result = self.trajectory.step(dt=dt, now_ns=now, stamp_ns=v['stamp'], vx=v['vx'], vy=v['vy'],
                                      body_yaw=self.odom['yaw'], joint_yaw=aim['yaw_rad'], joint_pitch=aim['pitch_rad'])
        if result is None:
            self.report = {'status': 'invalid_navigation_time'}; return None
        self.serial += 1
        command = hold_aim()
        command.update(yaw_type=1, pitch_type=1, yaw=result['yaw'], pitch=result['pitch'],
                       yaw_rate=result['yaw_rate']-self.odom['yaw_rate'], fire=0, permit=0)
        self.report = {'status': 'following' if result['moving'] else 'holding', **result,
                       'estimated_odom_stamp_ns': self.odom['stamp'], 'navigation_stamp_ns': v['stamp'],
                       'stamp_ns': now, 'fire_enabled': False, 'input_frame': self.node.robot_id+'/base_link'}
        self.entry={'stamp': now, 'command': command, 'received': time.monotonic(), 'serial': self.serial,
                    'frame_id': self.node.robot_id+'/base_link'}
        return self.entry
