"""Sentry navigation yaw trajectory adapted from pinned universal_controller.

Source: 95c66c28717e0b4d0f586c2ebf8df99d47a24ab0,
src/controllers/gimbal_controller.cpp::compute_navigation_yaw_control.
Only the high-level target shaper is reproduced here. The hardware PID/current
actuator and RC/vision arbitration remain outside this adapter. Inputs are Nav2
velocity, estimated body yaw and measured local joint feedback; never truth pose.
"""
from dataclasses import dataclass
import math


def wrap(value):
    return math.atan2(math.sin(value), math.cos(value))


def clamp(value, low, high):
    return min(high, max(low, value))


@dataclass(frozen=True)
class FollowConfig:
    min_yaw_rate: float = .3
    max_yaw_rate: float = 1.
    max_yaw_accel: float = 2.
    yaw_tolerance: float = .02
    min_speed: float = .03
    input_timeout_s: float = .2

    def __post_init__(self):
        for name, value in vars(self).items():
            if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                raise ValueError('Invalid navigation follow parameter: ' + name)
        if min(self.max_yaw_rate, self.max_yaw_accel, self.yaw_tolerance, self.min_speed, self.input_timeout_s) <= 0:
            raise ValueError('Follow limits must be positive')
        if self.min_yaw_rate > self.max_yaw_rate or self.min_yaw_rate**2 > 4*self.max_yaw_accel*self.yaw_tolerance:
            raise ValueError('Minimum follow rate cannot stop inside the original tolerance')


class NavigationYaw:
    def __init__(self, config=None):
        self.config = config or FollowConfig()
        self.reset()

    def reset(self):
        self.active = False
        self.target = 0.
        self.destination = 0.
        self.rate = 0.
        self.pitch = 0.
        self.command_stamp = -1
        self.moving = False

    def step(self, *, dt, now_ns, stamp_ns, vx, vy, body_yaw, joint_yaw, joint_pitch):
        c = self.config
        if not all(math.isfinite(v) for v in (dt, vx, vy, body_yaw, joint_yaw, joint_pitch)):
            self.reset(); return None
        age = (now_ns-stamp_ns)*1e-9
        if not 0 < dt <= c.input_timeout_s or stamp_ns <= 0 or stamp_ns < self.command_stamp or not -.05 <= age < c.input_timeout_s:
            self.reset(); return None
        if not self.active:
            self.target = wrap(body_yaw + joint_yaw)
            self.pitch = joint_pitch
            self.destination = self.target
            self.rate = 0.
            self.active = True
        if stamp_ns != self.command_stamp:
            self.moving = math.hypot(vx, vy) >= c.min_speed
            # Equivalent to original imu_yaw + atan2(v_y_gimbal, v_x_gimbal).
            # Our accepted velocity is already in standard ROS body coordinates.
            self.destination = wrap(body_yaw + math.atan2(vy, vx))
            self.command_stamp = stamp_ns
        error = wrap(self.destination-self.target)
        desired_rate = 0.
        if self.moving and abs(error) > c.yaw_tolerance:
            braking_rate = math.sqrt(2*c.max_yaw_accel*(abs(error)-c.yaw_tolerance))
            desired_rate = math.copysign(clamp(braking_rate, c.min_yaw_rate, c.max_yaw_rate), error)
        limit = c.max_yaw_accel*dt
        self.rate += clamp(desired_rate-self.rate, -limit, limit)
        self.target = wrap(self.target+self.rate*dt)
        return {'yaw': wrap(self.target-body_yaw), 'pitch': self.pitch,
                'target_in_odom': self.target, 'destination_in_odom': self.destination,
                'yaw_rate': self.rate, 'moving': self.moving}
