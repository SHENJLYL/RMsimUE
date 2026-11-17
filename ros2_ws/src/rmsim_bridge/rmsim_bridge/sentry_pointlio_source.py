"""Read original Point-LIO filtering parameters from the selected sentry_ws YAML.

Sensor units, live mounting transforms, gravity, and message topics remain
supplied by the actual vehicle hardware model instead of the reference YAML.
"""
import math
from pathlib import Path

import yaml

FIELDS = ('point_filter_num','filter_size_surf','filter_size_map','ivox_nearby_type','space_down_sample')


def pointlio_tuning(source):
    if not source:
        return {}
    path=Path(source)
    if not path.is_file() or path.stat().st_size > 2_000_000 or path.suffix.lower() not in ('.yaml','.yml'):
        raise ValueError('Invalid sentry Point-LIO configuration source')
    parsed=yaml.safe_load(path.read_text(encoding='utf-8'))
    values=parsed.get('point_lio',{}).get('ros__parameters',{}) if isinstance(parsed,dict) else {}
    if not values:
        return {}
    if not isinstance(values,dict) or not all(key in values for key in FIELDS):
        raise ValueError('Incomplete source Point-LIO filtering parameters')
    result={key:values[key] for key in FIELDS}
    if type(result['point_filter_num']) is not int or not 1<=result['point_filter_num']<=32:
        raise ValueError('Invalid source lidar point filter')
    for key in ('filter_size_surf','filter_size_map'):
        value=result[key]
        if type(value) not in (float,int) or not math.isfinite(value) or not .05<=value<=2.0:
            raise ValueError('Invalid source voxel filter: '+key)
    if type(result['ivox_nearby_type']) is not int or result['ivox_nearby_type'] not in (0,6,18,26):
        raise ValueError('Invalid source ivox neighborhood')
    if type(result['space_down_sample']) is not bool:
        raise ValueError('Invalid source voxel downsample flag')
    return result
