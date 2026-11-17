"""Convert the pinned Mid-360 Point-LIO raw-acceleration contract to ROS SI.

Estimator.cpp compares acc_avr with satu_acc BEFORE normalization by acc_norm.
The pinned mid360.yaml defaults describe a 3g limit with acc_norm=1. ROS Imu
samples are m/s^2, so both raw-value parameters must be converted together.
The estimator's standard-gravity constant is 9.81; scene gravity is separate.
"""
import math

STANDARD_GRAVITY = 9.81
PINNED_MID360_SATURATION_G = 3.0


def si_acceleration_parameters(saturation_g=PINNED_MID360_SATURATION_G):
    if type(saturation_g) not in (float, int) or not math.isfinite(saturation_g) or saturation_g <= 0:
        raise ValueError('Point-LIO acceleration range must be positive and finite')
    return {'mapping.acc_norm': STANDARD_GRAVITY,
            'mapping.satu_acc': saturation_g * STANDARD_GRAVITY}
