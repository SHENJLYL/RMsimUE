"""Cross-check native recovery evidence and currently installed inputs before release."""
import argparse
import hashlib
import json
from pathlib import Path


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--require-reset',action='store_true');args=parser.parse_args()
    root=Path(__file__).resolve().parents[2];base=args.evidence.resolve()
    required={'matching_map_identity_and_launched_processes','estimated_frame_and_scan_chain',
              'amcl_converges_from_perturbed_prior','amcl_has_no_truth_subscription','recovery_goal_accepted',
              'disconnect_stops_owned_navigation_stack','reconnect_rebuilds_estimated_navigation',
              'disconnect_physical_braking','reconnect_does_not_replay_interrupted_goal',
              'original_nav2_accepts_goal','real_vehicle_reaches_goal','original_planner_published_path'}
    runs=[]
    names=['release-01','release-02']
    if args.require_reset:names.append('reset-01')
    for name in names:
        file=base/name/'ros/verification.json';data=read(file)
        expect=required|({'return_goal_accepted','active_goal_cancelled','cancel_stops_physical_vehicle',
                          'reset_rebuilds_navigation_and_localization','reset_does_not_replay_old_goal'}
                         if name=='reset-01' else {'round_trip_return_accepted','round_trip_reaches_return_goal'})
        actual={c['name'] for c in data['checks']}
        if data['status']!='passed' or actual!=expect or len(data['checks'])!=len(expect) or not all(c['passed'] for c in data['checks']):
            raise RuntimeError('Incomplete native acceptance: '+name)
        state=read(base/name/'ros/pipeline-diagnostics.json')['samples'][-1]['navigation']
        if state.get('transport',{}).get('mode')!='loopback_udp' or state['transport']['allowed_interfaces']!=['127.0.0.1']:
            raise RuntimeError('Actual navigation transport was not the accepted local profile')
        runs.append({'name':name,'checks':len(data['checks']),'verification_sha256':sha(file),
                     'metrics':[c for c in data['checks'] if c['name'] in {
                         'reconnect_rebuilds_estimated_navigation','disconnect_physical_braking',
                         'real_vehicle_reaches_goal','round_trip_reaches_return_goal',
                         'reset_rebuilds_navigation_and_localization','cancel_stops_physical_vehicle'}]})
    provenance=read(base/'release-02/runtime-provenance.json')
    for name,expected in provenance['navigation_module_sha256'].items():
        if sha(root/'artifacts/runtime/navigation/windows'/name)!=expected.lower():raise RuntimeError('Native DLL changed: '+name)
    for name,expected in provenance['installed_bridge_sha256'].items():
        installed=root/'ros2_ws/install_bridge/rmsim_bridge/Lib/site-packages/rmsim_bridge'/name
        source=root/'ros2_ws/src/rmsim_bridge/rmsim_bridge'/name
        if sha(installed)!=expected.lower() or sha(installed)!=sha(source):raise RuntimeError('Installed sidecar mismatch: '+name)
    for key,relative in [('module_sha256','unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll'),
                         ('drive_profile_sha256','assets/vehicles/aim_sentry/drive-profile.json'),
                         ('rig_sha256','assets/vehicles/aim_sentry/ue_mesh_rig.json')]:
        if sha(root/relative)!=provenance[key].lower():raise RuntimeError('Simulator input changed: '+relative)
    fixtures=[]
    for name,count in [('startup-after',4),('loam-startup-after',4),('transport-default',6),('transport-loopback_udp',6)]:
        data=read(base/name/'verification.json')
        if data['status']!='passed' or len(data['checks'])!=count or not all(c['passed'] for c in data['checks']):raise RuntimeError('Fixture failed: '+name)
        fixtures.append({'name':name,'checks':count,'scope':data.get('scope','synthetic native interface fixture')})
    offline=read(base/'final-offline/verification.json')
    if offline['status']!='passed':raise RuntimeError('Offline checks failed')
    if args.require_reset:
        gui=read(base/'reset-01/gui-reset/verification.json')
        if gui['status']!='input_sent' or gui['keydown_count']!=1 or gui['keyup_count']!=1:raise RuntimeError('Actual R input was not recorded')
    report={'status':'native_reconnect_and_reset_verified' if args.require_reset else 'native_reconnect_verified_reset_pending',
            'scope':'Windows original Point-LIO/AMCL/Nav2, local transport, current 2000/140 AIM suspension and covered routes',
            'runs':runs,'native_navigation_checks':sum(r['checks'] for r in runs),'native_interface_fixtures':fixtures,
            'offline':offline,'current_inputs_match_launch_provenance':True,'launch_provenance':provenance,
            'failed_default_transport_runs':read(base/'diagnostic-failures.json'),
            'limitations':['Native Ubuntu not run','Uncalibrated sensors and dynamics','Whole-field and multi-robot navigation remain separate acceptance']}
    (base/'release-summary.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','native_navigation_checks','current_inputs_match_launch_provenance')},indent=2))


if __name__=='__main__':main()
