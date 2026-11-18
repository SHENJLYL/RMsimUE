"""Snapshot supported Point-LIO tuning from the selected sentry_ws YAML.

Sensor units, live mounting transforms, gravity, and message topics remain
supplied by the actual vehicle hardware model. A runner keeps one validated
snapshot for its lifetime, including session/epoch restarts.
"""
import hashlib
import math
from pathlib import Path

import yaml

FIELDS = ('point_filter_num','filter_size_surf','filter_size_map','ivox_nearby_type','space_down_sample')
MAX_SOURCE_BYTES = 2_000_000


def _validated_tuning(parsed):
    if not isinstance(parsed,dict):
        raise ValueError('sentry Point-LIO YAML root must be a mapping')
    if 'point_lio' not in parsed:
        return {}
    block=parsed['point_lio']
    if not isinstance(block,dict):
        raise ValueError('point_lio must be a mapping')
    values=block.get('ros__parameters',{})
    if not isinstance(values,dict):
        raise ValueError('point_lio.ros__parameters must be a mapping')
    if not values:
        return {}
    if not all(key in values for key in FIELDS):
        raise ValueError('Incomplete source Point-LIO filtering parameters')
    result={key:values[key] for key in FIELDS}
    common=values.get('common',{})
    if not isinstance(common,dict):
        raise ValueError('point_lio.ros__parameters.common must be a mapping')
    depth=common.get('imu_qos_depth',5)
    if type(depth) is not int or not 1<=depth<=4096:
        raise ValueError('common.imu_qos_depth must be an integer in [1, 4096]')
    result['common.imu_qos_depth']=depth
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
    # Consume the original estimator's uncertainty and IMU saturation tuning.
    # Gravity/extrinsics/topics remain measured from the live vehicle profile.
    for key in ('check_satu', 'use_imu_as_input', 'prop_at_freq_of_imu'):
        value=values.get(key)
        if type(value) is not bool:
            raise ValueError('Invalid original Point-LIO boolean: '+key)
        result[key]=value
    mapping=values.get('mapping')
    if not isinstance(mapping,dict):
        raise ValueError('point_lio.ros__parameters.mapping must be a mapping')
    source_acc_norm=mapping.get('acc_norm')
    source_saturation=mapping.get('satu_acc')
    if any(type(v) not in (float,int) or not math.isfinite(v) or v<=0 for v in (source_acc_norm,source_saturation)):
        raise ValueError('Invalid original IMU source units/saturation')
    saturated_si=float(source_saturation)*9.81/float(source_acc_norm)
    if not 1.0<=saturated_si<=500.0:
        raise ValueError('Unsupported physical IMU saturation in SI')
    result['mapping.satu_acc']=saturated_si
    # Estimator.cpp forms z_IMU after G_m_s2/acc_norm normalization. Its R_IMU
    # covariances describe that normalized residual and do not scale with the
    # raw input's g-versus-SI convention; the saturation check above does.
    for key in ('lidar_meas_cov','imu_meas_acc_cov','imu_meas_omg_cov','ivox_grid_resolution'):
        value=mapping.get(key)
        if type(value) not in (float,int) or not math.isfinite(value) or not 1e-6<=value<=100:
            raise ValueError('Invalid original Point-LIO mapping tuning: '+key)
        result['mapping.'+key]=float(value)
    return result


def pointlio_source_snapshot(source):
    """Read, validate and hash the same bytes exactly once; no live file handle."""
    if not source:
        return {'path':None,'sha256':None,'parameters':{}}
    path=Path(source).resolve()
    if not path.is_file() or path.stat().st_size>MAX_SOURCE_BYTES or path.suffix.lower() not in ('.yaml','.yml'):
        raise ValueError('Invalid sentry Point-LIO configuration source')
    # Bound the read itself as well as the stat, including a concurrent rewrite.
    with path.open('rb') as stream:
        raw=stream.read(MAX_SOURCE_BYTES+1)
    if len(raw)>MAX_SOURCE_BYTES:
        raise ValueError('sentry Point-LIO configuration exceeds size limit')
    try:
        parsed=yaml.safe_load(raw.decode('utf-8-sig'))
    except (UnicodeDecodeError,yaml.YAMLError) as error:
        raise ValueError('Cannot parse sentry Point-LIO YAML: '+str(path)) from error
    tuning=_validated_tuning(parsed)
    return {'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'parameters':tuning}


def pointlio_tuning(source):
    """Compatibility API for one-shot preparation; runners cache the snapshot."""
    return pointlio_source_snapshot(source)['parameters']
