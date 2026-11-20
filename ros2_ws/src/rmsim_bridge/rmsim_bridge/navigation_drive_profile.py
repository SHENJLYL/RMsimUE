"""Bind simulator navigation limits to the same vehicle profile as manual driving."""
import copy
import hashlib
import json
import math
from pathlib import Path


REQUIRED_CONTROL = (
    'MaxForwardSpeedMps', 'MaxReverseSpeedMps', 'MaxLateralSpeedMps',
    'LinearAccelerationMps2', 'LinearDecelerationMps2', 'BrakeDecelerationMps2',
    'LinearJerkMps3', 'DecelerationJerkMps3', 'ManualYawRateRadS',
    'AngularAccelerationRadS2', 'AngularDecelerationRadS2',
    'LateralAccelerationMps2', 'DirectionJerkMps3',
)


def load_drive_profile(filename):
    path = Path(filename).resolve()
    if not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('Navigation requires a bounded vehicle drive profile')
    raw = path.read_bytes()
    data = json.loads(raw.decode('utf-8-sig'))
    if (not isinstance(data, dict) or data.get('schema') != 'rm_drive_profile_v1'
            or data.get('backend') != 'swerve_contact_v1' or data.get('units') != 'SI'):
        raise ValueError('Unsupported vehicle drive profile schema, backend or units')
    control = data.get('control')
    if not isinstance(control, dict) or not set(REQUIRED_CONTROL).issubset(control):
        raise ValueError('Vehicle drive profile is missing shared motion controls')
    for name, value in control.items():
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('Invalid vehicle control: ' + str(name))
    for name in REQUIRED_CONTROL:
        if control[name] <= 0:
            raise ValueError('Shared motion limit must be positive: ' + name)
    forward, reverse, lateral, yaw = (control[name] for name in (
        'MaxForwardSpeedMps', 'MaxReverseSpeedMps', 'MaxLateralSpeedMps', 'ManualYawRateRadS'))
    acceleration = control['LinearAccelerationMps2']
    deceleration = control['LinearDecelerationMps2']
    return {
        'schema': 'rm_navigation_drive_profile_v1',
        'source_path': str(path), 'source_sha256': hashlib.sha256(raw).hexdigest(),
        'source_schema': data['schema'], 'source_backend': data['backend'],
        'vehicle_control': copy.deepcopy(control),
        'controller_limits': {
            'v_linear_max': forward, 'v_linear_min': -reverse,
            'v_angular_max': yaw, 'v_angular_min': -yaw,
        },
        'smoother_limits': {
            'max_velocity': [forward, lateral, yaw],
            'min_velocity': [-reverse, -lateral, -yaw],
            'max_accel': [acceleration, acceleration, control['AngularAccelerationRadS2']],
            'max_decel': [-deceleration, -deceleration, -control['AngularDecelerationRadS2']],
        },
        'vehicle_shaper': 'shared_manual_navigation_contact_drive',
        'velocity_smoother_active': True,
        'smoother_frame': 'base_link',
        'adapter_clock': 'ROS /clock',
        'direction_lateral_acceleration_owner': 'vehicle_shaper',
    }


def velocity_routes(robot):
    prefix = '/' + robot + '/navigation/'
    topics = {
        'navigation_raw': prefix + 'cmd_vel_navigation',
        'body_stamped': prefix + 'cmd_vel_body_stamped',
        'body_raw': prefix + 'cmd_vel_body',
        'body_smoothed': prefix + 'cmd_vel_body_smoothed',
        'public': '/' + robot + '/cmd_vel',
    }
    remaps = {
        'controller_server': [('cmd_vel', topics['navigation_raw'])],
        'behavior_server': [('cmd_vel', topics['navigation_raw'])],
        'velocity_smoother': [('cmd_vel', topics['body_raw']),
                              ('cmd_vel_smoothed', topics['body_smoothed'])],
    }
    return topics, remaps


def apply_drive_profile(config, filename, robot):
    identity = load_drive_profile(filename)
    controller = config['controller_server']['ros__parameters']['FollowPath']
    if controller.get('plugin') != 'pb_omni_pid_pursuit_controller::OmniPidPursuitController':
        raise ValueError('Shared vehicle motion profile requires the selected OmniPid controller')
    controller.update(copy.deepcopy(identity['controller_limits']))
    config['velocity_smoother']['ros__parameters'].update(copy.deepcopy(identity['smoother_limits']))
    topics, remaps = velocity_routes(robot)
    config['fake_vel_transform']['ros__parameters'].update(
        input_cmd_vel_topic=topics['navigation_raw'], output_cmd_vel_topic=topics['body_stamped'])
    identity.update(topics=topics, topic_remaps=remaps,
                    command_chain=['controller_or_behavior_navigation_frame',
                                   'fake_vel_transform_to_base_link', 'runner_unstamp',
                                   'original_velocity_smoother', 'runner_sim_clock_stamp',
                                   'public_body_cmd_vel', 'shared_vehicle_shaper'])
    return identity
