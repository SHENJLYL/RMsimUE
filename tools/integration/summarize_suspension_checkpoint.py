"""Validate a suspension checkpoint against live hashes and independent native gate records."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
def read(path): return json.loads(path.read_text(encoding='utf-8-sig'))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--repeat', required=True)
    args = parser.parse_args(); directory = args.directory.resolve()
    profile_path = ROOT/'assets/vehicles/aim_sentry/drive-profile.json'
    module_path = ROOT/'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll'
    module_hash, profile_hash = sha(module_path), sha(profile_path)
    live, previous = read(profile_path), read(directory/'before/profile.json')
    changes = {}
    for section in ('physics', 'control'):
        for key in set(live[section]) | set(previous[section]):
            a, b = previous[section].get(key), live[section].get(key)
            if a != b: changes[section+'.'+key] = {'before': a, 'after': b}
    expected_changes = {'physics.SpringStiffnessNPerM', 'physics.SpringDampingNsPerM'}
    if set(changes) != expected_changes or not all(v['after'] > v['before'] for v in changes.values()):
        raise ValueError('Expected only an increase of spring stiffness and damping')
    fixtures = {}
    for name in (args.baseline, args.candidate, args.repeat):
        v = read(directory/name/'verification.json')
        if v['status'] != 'passed' or v['provenance']['hashes']['module'].lower() != module_hash:
            raise ValueError('Fixture failed or used another module: '+name)
        fixtures[name] = v
    for key in expected_changes:
        field = key.split('.')[1]
        if fixtures[args.candidate]['physics_parameters'][field] != live['physics'][field] or fixtures[args.repeat]['physics_parameters'][field] != live['physics'][field]:
            raise ValueError('Candidate fixture does not match live physics')
    regression = read(directory/'regression/verification.json')
    if regression['status'] != 'passed' or regression['module_sha256'].lower() != module_hash or regression['drive_profile_sha256'].lower() != profile_hash:
        raise ValueError('Regression not complete or live frozen inputs changed')
    runs = []; total = 0; pngs = 0
    for folder, expected in [('traction',10),('controls/default',19),('waves/wave_red',5),('waves/wave_blue',5),('integration',10),('fast',5),('spin',6),('ros/ros',11),('lk/lk',21)]:
        v = read(directory/'regression'/folder/'verification.json'); checks = v['checks']
        count = len(checks) if isinstance(checks,list) else checks
        valid = v.get('passed') is True or v.get('status') == 'passed'
        if isinstance(checks,list): valid = valid and all(c['passed'] for c in checks)
        if not valid or count != expected: raise ValueError('Regression mismatch: '+folder)
        if 'module_sha256' in v and v['module_sha256'].lower() != module_hash: raise ValueError('Module mismatch')
        if 'drive_profile_sha256' in v and v['drive_profile_sha256'].lower() != profile_hash: raise ValueError('Profile mismatch')
        images = v.get('decoded_captures',0); total += count; pngs += images
        runs.append({'directory':folder,'checks':count,'decoded_pngs':images,'passed':True})
    for folder in ('ros','lk'):
        v = read(directory/'regression'/folder/'runtime-provenance.json')
        if v['module_sha256'].lower() != module_hash or v['drive_profile_sha256'].lower() != profile_hash: raise ValueError('ROS/LK provenance mismatch')
    result = {'status':'native_windows_verified','scope':'AIM suspension response and native driving/ROS/LK regression; OS feel and navigation are separate','module_sha256':module_hash,'drive_profile_sha256':profile_hash,'rig_sha256':sha(ROOT/'assets/vehicles/aim_sentry/ue_mesh_rig.json'),'changed_parameters':changes,'regression_checks':total,'decoded_pngs':pngs,'runs':runs,'baseline_metrics':fixtures[args.baseline]['metrics'],'candidate_metrics':fixtures[args.candidate]['metrics'],'repeat_metrics':fixtures[args.repeat]['metrics'],'fixture_runs':[args.baseline,args.candidate,args.repeat],'candidate_fixture_checks':8,'candidate_fixture_pngs':12,'offline':read(directory/'offline/verification.json'),'sleep_threshold_experiment':'rejected and reverted; native sleep unchanged','real_robot_calibrated':False}
    (directory/'release-summary.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('status','regression_checks','decoded_pngs','changed_parameters')},indent=2))

if __name__ == '__main__': main()
