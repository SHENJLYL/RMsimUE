"""Compact, reproducible checkpoint for experimental sentry_ws-owned speed."""
import hashlib
import json
from pathlib import Path
import subprocess
import yaml

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'artifacts/navigation/nav-speed-20261117'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    src=ROOT/'ros2_ws/src/pb2025_nav_bringup/config/simulation/nav2_params.yaml'
    original=ROOT/'third_party/sentry_nav_original/pb2025_nav_bringup/config/simulation/nav2_params.yaml'
    source=yaml.safe_load(src.read_text(encoding='utf-8'))
    upstream=yaml.safe_load(original.read_text(encoding='utf-8'))
    follow=source['controller_server']['ros__parameters']['FollowPath']
    smoother=source['velocity_smoother']['ros__parameters']
    physical=read(ROOT/'assets/vehicles/aim_sentry/drive-profile.json')['control']
    assert follow['v_linear_max']==physical['MaxForwardSpeedMps']==3.
    assert smoother['max_velocity'][:2]==[physical['MaxForwardSpeedMps'],physical['MaxLateralSpeedMps']]
    assert smoother['min_velocity'][:2]==[-physical['MaxReverseSpeedMps'],-physical['MaxLateralSpeedMps']]
    assert smoother['max_accel'][:2]==[physical['LinearAccelerationMps2']]*2
    assert smoother['max_decel'][:2]==[-physical['LinearDecelerationMps2']]*2
    assert follow['curvature_min']==upstream['controller_server']['ros__parameters']['FollowPath']['curvature_min']
    assert follow['curvature_max']==upstream['controller_server']['ros__parameters']['FollowPath']['curvature_max']
    assert follow['reduction_ratio_at_high_curvature']==upstream['controller_server']['ros__parameters']['FollowPath']['reduction_ratio_at_high_curvature']
    for name in ('local_costmap','global_costmap'):
        assert source[name]==upstream[name], 'Source obstacle map diverged: '+name
    assert source['planner_server']==upstream['planner_server']
    assert source['point_lio']==upstream['point_lio']
    build=read(BASE/'bridge-build-lio-source/verification.json')
    assert build['status']=='passed'
    for item in build['installed']:
        src=ROOT/'ros2_ws/src/rmsim_bridge/rmsim_bridge'/item['file']
        target=ROOT/'ros2_ws/install_bridge/rmsim_bridge/Lib/site-packages/rmsim_bridge'/item['file']
        assert sha(src)==sha(target)==item['installed_hash'],item['file']
    offline=read(BASE/'offline-final-01/verification.json')
    assert offline['status']=='passed'
    trials={}
    for run in ('step10ms-native-01','step5ms-native-01','approach-step5ms-native-01',
                'smoother-step5ms-native-01','held-gimbal-native-01','all-source-step5ms-native-01'):
        path=BASE/run/'verification.json'
        d=read(path)
        trials[run]={'status':d['status'],'failed_check':next((v['name'] for v in d['checks'] if not v['passed']),None),
                     'check_count':len(d['checks']),
                     'single_error_m':next((v.get('error_m') for v in d['checks'] if v['name']=='single_goal_actual_arrival'),None),
                     'waypoint_error_m':next((v.get('error_m') for v in d['checks'] if v['name']=='waypoint_sequence_actual_arrival'),None),
                     'sha256':sha(path)}
    assert all(d['status']=='failed' for d in trials.values())
    source_path=ROOT/'ros2_ws/src/pb2025_nav_bringup/config/simulation/nav2_params.yaml'
    report={'status':'in_progress_high_speed','source_of_truth':'working sentry_ws simulation/nav2_params.yaml',
            'source_path':source_path.relative_to(ROOT).as_posix(),'source_sha256':sha(source_path),
            'upstream_unchanged_sha256':sha(original),
            'manual_limits_mps':{'forward':3.,'reverse':1.5,'lateral':2.},
            'native_planner_smoother_costmap_from_source':True,
            'native_pointlio_filtering_from_source':True,
            'source_to_installed_python_verified':True,
            'offline':{'passed':offline['passed'],'skipped':offline['skipped'],'tests_run':offline['tests_run']},
            'trials':trials,
            'realtime_measurement':read(BASE/'smoother-step5ms-clock-01/verification.json'),
            'full_route_accepted':False,
            'limitations':['Repeated high-speed waypoint route fails independent actual-position acceptance',
                           'Physics/sensor clock observed slower than real time under load',
                           'Full automatic vehicle-package import still pending; external navigation YAML path available'],
            'scope':'Standalone Windows native source integration and real UE/ROS tests; no truth fed to control'}
    (BASE/'checkpoint-summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','source_path','manual_limits_mps','offline','full_route_accepted')},indent=2))


if __name__=='__main__':
    main()
