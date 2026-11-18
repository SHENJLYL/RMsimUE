"""Select pinned sentry_ws navigation behavior directly from its source YAML.

The selected profile owns navigation, smoothing and avoidance, and may supply
complete AMCL and LaserScan parameter blocks. Older profiles without those
optional blocks retain reference-reality localization. RMSim applies frame,
clock and measured-footprint adaptation after source selection.
"""
import copy
import hashlib
from pathlib import Path
import yaml

MODES = ('legacy', 'reality', 'simulation')
NAVIGATION_BLOCKS = ('controller_server', 'velocity_smoother',
                     'planner_server', 'local_costmap', 'global_costmap')
OPTIONAL_LOCALIZATION_BLOCKS = ('amcl', 'pointcloud_to_laserscan')


def source_profile(reference, mode, explicit_source=''):
    if mode not in MODES:
        raise ValueError('Unrecognized sentry navigation profile')
    if mode == 'legacy':
        if explicit_source:
            raise ValueError('Explicit navigation source requires sentry profile mode')
        return {}, {'mode': mode, 'source_path': '', 'source_sha256': ''}
    reference = Path(reference).resolve()
    root = reference.parent.parent
    source = Path(explicit_source).resolve() if explicit_source else (root / mode / 'nav2_params.yaml').resolve()
    if source.suffix.lower() not in ('.yaml','.yml') or source.is_file() and source.stat().st_size > 2_000_000:
        raise ValueError('Navigation source must be a bounded YAML file')
    if not source.is_file():
        raise ValueError('Sentry navigation source profile is missing: '+str(source))
    parsed = yaml.safe_load(source.read_text(encoding='utf-8'))
    if not isinstance(parsed, dict):
        raise ValueError('Sentry navigation profile must contain nodes')
    blocks = {}
    for name in NAVIGATION_BLOCKS:
        block = parsed.get(name)
        params = block.get('ros__parameters') if name not in ('local_costmap','global_costmap') and isinstance(block,dict) else block.get(name,{}).get('ros__parameters') if isinstance(block,dict) else None
        if not isinstance(params, dict):
            raise ValueError('Incomplete original sentry navigation block: '+name)
        blocks[name] = copy.deepcopy(block)
    for name in OPTIONAL_LOCALIZATION_BLOCKS:
        if name not in parsed:
            continue
        block = parsed[name]
        if not isinstance(block, dict) or not isinstance(block.get('ros__parameters'), dict):
            raise ValueError(
                'Invalid optional sentry localization block ' + name +
                ': expected a node mapping with a ros__parameters mapping')
        blocks[name] = copy.deepcopy(block)
    controller = blocks['controller_server']['ros__parameters']['FollowPath']
    smoother = blocks['velocity_smoother']['ros__parameters']
    if controller.get('plugin') != 'pb_omni_pid_pursuit_controller::OmniPidPursuitController':
        raise ValueError('Unexpected original sentry path controller')
    if not all(isinstance(smoother.get(key), list) and len(smoother[key]) == 3
               for key in ('max_velocity','min_velocity','max_accel','max_decel')):
        raise ValueError('Invalid original sentry velocity smoother')
    for name in ('local_costmap','global_costmap'):
        settings = blocks[name][name]['ros__parameters']
        for layer in ('static_layer','intensity_voxel_layer','inflation_layer'):
            if layer not in settings.get('plugins',[]) or layer not in settings:
                raise ValueError('Original collision avoidance layer missing: '+layer)
    return blocks, {'mode':mode, 'source_path':str(source),
                    'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
