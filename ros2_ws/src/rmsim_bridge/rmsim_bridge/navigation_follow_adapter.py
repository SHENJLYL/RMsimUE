"""Fresh estimated-odometry adapter for pinned sentry navigation yaw following."""
import json
import math
from pathlib import Path
import time
from nav_msgs.msg import Odometry
from rclpy.qos import qos_profile_sensor_data
from .navigation_follow import FollowConfig, NavigationYaw, clamp
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
        self.world_pitch_anchor = None
        self.report = {'status': 'waiting_for_estimated_odometry'}
        self.subscription = node.create_subscription(Odometry, '/odometry', self.on_odometry, qos_profile_sensor_data)

    def reset(self):
        self.trajectory.reset()
        self.entry = None
        self.velocity = None
        self.odom = None
        self.last_time = None
        self.report = {'status': 'waiting_for_fresh_session_inputs'}
        self.world_pitch_anchor = None

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
        twist = msg.twist.twist
        yaw_rate = twist.angular.z
        if math.isfinite(yaw_rate):
            self.odom = {'stamp': stamp, 'yaw': yaw, 'yaw_rate': yaw_rate,
                         'vx': twist.linear.x, 'vy': twist.linear.y}

    def observe_velocity(self, msg):
        # Called only after the normal bridge validates and queues this command.
        self.velocity = {'stamp': msg.header.stamp.sec*1_000_000_000+msg.header.stamp.nanosec,
                         'vx': msg.twist.linear.x, 'vy': msg.twist.linear.y,
                         'frame': msg.header.frame_id, 'wall': time.monotonic()}

    def poll(self, overridden=False, *, scan_ready=False, measured_motion=False):
        state = self.node.state
        now = state['sim_time_ns']
        previous, self.last_time = self.last_time, now
        dt = (now-previous)*1e-9 if previous is not None else 0.
        config = self.trajectory.config
        timeout = config.input_timeout_s
        odom_age = (now-self.odom['stamp'])*1e-9 if self.odom else math.inf
        v = self.velocity
        command_age = (now-v['stamp'])*1e-9 if v else math.inf
        command_fresh = -.05 <= command_age < timeout and time.monotonic()-v['wall'] <= timeout
        # During stationary gyro, the odometry lever-arm correction can report translation noise.
        # A live Nav2 translation enables following; its velocity never supplies the match heading.
        translation_requested = command_fresh and math.hypot(v['vx'], v['vy']) >= config.min_speed
        measured_fresh = (measured_motion and translation_requested and -.05 <= odom_age < timeout
                          and all(math.isfinite(self.odom[axis]) for axis in ('vx', 'vy')))
        moving = measured_fresh and math.hypot(self.odom['vx'], self.odom['vy']) >= config.min_speed
        if overridden or (scan_ready and not moving):
            self.trajectory.reset(); self.entry=None; self.world_pitch_anchor=None
            self.report = {'status': 'higher_priority_aim'}; return None
        if not self.odom or (not measured_motion and not self.velocity):
            return None
        # Scan odometry may miss a publication while the gyro is spinning.
        # Bound the existing rate extrapolation to two command timeouts.
        if not -.05 <= odom_age < 2 * timeout:
            if odom_age >= 2 * timeout:
                self.trajectory.reset()
                self.world_pitch_anchor = None
            self.entry = None
            self.report = {'status': 'stale_estimated_odometry', 'odometry_age_s': odom_age}
            return None
        if dt==0:return self.entry
        aim = state['aim']
        yaw_rate = self.odom['yaw_rate']
        body_yaw = self.odom['yaw'] + yaw_rate * odom_age
        if measured_fresh:
            result = self.trajectory.step(dt=dt, now_ns=now, stamp_ns=self.odom['stamp'],
                                          vx=self.odom['vx'], vy=self.odom['vy'], body_yaw=body_yaw,
                                          command_frame_yaw=self.odom['yaw'], joint_yaw=aim['yaw_rad'],
                                          joint_pitch=aim['pitch_rad'])
        elif command_fresh and not measured_motion:
            command_yaw = (0.0 if v['frame'] == self.node.robot_id+'/odom'
                           else body_yaw - yaw_rate * command_age)
            result = self.trajectory.step(dt=dt, now_ns=now, stamp_ns=v['stamp'], vx=v['vx'], vy=v['vy'],
                                          body_yaw=body_yaw, command_frame_yaw=command_yaw,
                                          joint_yaw=aim['yaw_rad'], joint_pitch=aim['pitch_rad'])
        else:
            result = self.trajectory.hold(dt=dt, body_yaw=body_yaw)
        if result is None:
            self.entry = None
            self.report = {'status': 'invalid_navigation_time'}; return None
        self.serial += 1
        command = hold_aim()
        yaw_limit = aim['yaw_rate_limit']
        joint_rate = clamp(result['yaw_rate']-yaw_rate, -yaw_limit, yaw_limit)
        # Keep the existing body-frame yaw trajectory and stabilize pitch from measured gimbal feedback.
        world_pitch = aim['world_pitch_rad']
        if self.world_pitch_anchor is None:
            self.world_pitch_anchor = world_pitch
        pitch = clamp(aim['pitch_rad']+self.world_pitch_anchor-world_pitch, aim['pitch_min_rad'], aim['pitch_max_rad'])
        command.update(yaw_type=1, pitch_type=1, yaw=result['yaw'], pitch=pitch,
                       yaw_rate=joint_rate, fire=0, permit=0)
        self.report = {'status': 'following' if result['moving'] else 'holding', **result,
                       'estimated_odom_stamp_ns': self.odom['stamp'], 'navigation_stamp_ns': v['stamp'] if v else None,
                       'stamp_ns': now, 'fire_enabled': False,
                       'input_frame': self.node.robot_id+'/base_link' if measured_motion else v['frame'],
                       'motion_source': 'estimated_odometry' if measured_motion else 'navigation_command',
                       'odometry_age_s': odom_age, 'navigation_command_fresh': command_fresh,
                       'world_yaw_rate_limit_rad_s': self.trajectory.config.max_yaw_rate,
                       'held_world_pitch_rad': self.world_pitch_anchor, 'stabilized_joint_pitch_rad': pitch,
                       'joint_yaw_rate_rad_s': joint_rate}
        self.entry={'stamp': now, 'command': command, 'received': time.monotonic(), 'serial': self.serial,
                    'frame_id': self.node.robot_id+'/base_link'}
        return self.entry
