"""Verify accepted native and GUI evidence before publishing the sentry console."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'artifacts/navigation/sentry-console-20261117'

def sha(p: Path) -> str:return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p: Path):return json.loads(p.read_text(encoding='utf-8-sig'))
def semantic_sha(data):return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
def record(path: Path):
    data=read(path)
    if data.get('status')!='passed' and data.get('passed') is not True:raise RuntimeError('Unpassed gate: '+str(path))
    checks=data.get('checks',[])
    if isinstance(checks,list):
        if not checks or not all(c.get('passed') is True for c in checks):raise RuntimeError('Incomplete checks: '+str(path))
        count=len(checks)
    else:count=checks
    if not isinstance(count,int) or count<1:raise RuntimeError('Missing native check count: '+str(path))
    return {'path':path.relative_to(ROOT).as_posix(),'checks':count,'sha256':sha(path),'semantic_sha256':semantic_sha(data)}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session',default='console-08')
    parser.add_argument('--gui',default='gui-acceptance-08')
    args=parser.parse_args()
    native_paths=['native-actions-02/verification.json','regression-01/mid360/tcp/verification.json',
                  'regression-01/manual/default/verification.json','regression-01/ros/ros/verification.json',
                  'regression-01/aim/aim/verification.json','regression-01/lidar-lk/ros/verification.json']
    gates=[record(BASE/p) for p in native_paths]
    gui=read(BASE/args.gui/'verification.json')
    if gui.get('status')!='passed' or {r['mode'] for r in gui['runs']}!={'single','waypoints','cancel'}:
        raise RuntimeError('All three real GUI modes must pass')
    gui_gates=[record(BASE/args.gui/r['mode']/'verification.json') for r in gui['runs']]
    offline_path=BASE/'final-offline-02/verification.json';offline=read(offline_path)
    if offline.get('status')!='passed' or offline['errors'] or offline['failures']:raise RuntimeError('Offline suite failed')
    provenance=read(BASE/args.session/'runtime-provenance.json')
    paths={'module_sha256':'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll',
           'drive_profile_sha256':'assets/vehicles/aim_sentry/drive-profile.json',
           'rig_sha256':'assets/vehicles/aim_sentry/ue_mesh_rig.json',
           'lidar_sha256':'assets/vehicles/aim_sentry/sensors/mid360.json',
           'imu_sha256':'assets/vehicles/aim_sentry/sensors/mid360-imu.json',
           'navigation_follow_sha256':'assets/vehicles/aim_sentry/navigation-follow.json'}
    for key,path in paths.items():
        if sha(ROOT/path)!=provenance[key].lower():raise RuntimeError('Runtime input changed: '+path)
    for name,expected in provenance['native_navigation'].items():
        if sha(ROOT/'artifacts/runtime/navigation/windows'/name)!=expected:raise RuntimeError('Navigation DLL changed: '+name)
    bridge_root=ROOT/'ros2_ws/install_bridge/rmsim_bridge/Lib/site-packages/rmsim_bridge'
    for name,expected in provenance['bridge_inputs'].items():
        installed=bridge_root/name;source=ROOT/'ros2_ws/src/rmsim_bridge/rmsim_bridge'/name
        if sha(installed)!=expected['installed_sha256'] or source.read_text(encoding='utf-8')!=installed.read_text(encoding='utf-8'):
            raise RuntimeError('Installed bridge changed: '+name)
    for p in ['regression-01/mid360/runtime-provenance.json','regression-01/ros/runtime-provenance.json','regression-01/aim/runtime-provenance.json','regression-01/lidar-lk/runtime-provenance.json']:
        d=read(BASE/p)
        for k in ('module_sha256','drive_profile_sha256'):
            if d[k].lower()!=provenance[k].lower():raise RuntimeError('Regression input mismatch: '+p)
    source=read(ROOT/'assets/vehicles/aim_sentry/sensors/mid360-source.json')
    unchanged=sha(Path('E:/WarThunderModding/war_thunder_sentry/aim_sentry.blend'))==source['source_blend_sha256']
    if not unchanged:raise RuntimeError('Source vehicle model changed')
    result={'status':'native_windows_console_verified','native_checks':sum(x['checks'] for x in gates),
            'gui_checks':sum(x['checks'] for x in gui_gates),'native_runs':gates,'gui_runs':gui_gates,
            'gui_route':{'target':gui['target'],'return_point':gui['return_point']},
            'offline':{'tests_run':offline['tests_run'],'passed':offline['passed'],'skipped':offline['skipped'],'path':offline_path.relative_to(ROOT).as_posix()},
            'runtime_inputs_match':True,'runtime_provenance':provenance,'source_blend_unchanged':True,
            'native_route_metrics':read(BASE/'native-actions-02/verification.json')['checks'],
            'scope':'single Windows robot; actual RViz UI, original Nav2, original yaw target shaping and articulated ideal Mid-360; independent truth evaluation only',
            'remaining':['Native Ubuntu execution','Factory optical/IMU and actuator calibration','Full hardware gimbal PID/Hub and visual autoaim','Arbitrary RMUC routes and multi-robot authoritative matches','Nav Through Poses button separate acceptance'],
            'preserved_failures':['native-actions-01: raw SI acceleration saturation units','native-console-02: startup component failure','GUI earlier runs: camera projection/tool activation and coarse zoom point error; original thresholds retained']}
    (BASE/'release-summary.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('status','native_checks','gui_checks','offline','runtime_inputs_match')},indent=2))

if __name__=='__main__':main()
