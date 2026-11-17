"""Compare a real UE map report and its exported occupancy against the source description."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rm_map_tools.map import load_map


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--map', required=True)
    p.add_argument('--runtime', required=True, help='folder containing map-report.json and motion/probe-result.json')
    p.add_argument('--occupancy', required=True)
    a = p.parse_args()
    data, digest = load_map(a.map)
    folder = Path(a.runtime)
    runtime = json.loads((folder/'map-report.json').read_text(encoding='utf-8-sig'))
    motion = json.loads((folder/'motion/probe-result.json').read_text(encoding='utf-8-sig'))
    grid_dir = Path(a.occupancy)
    metadata = json.loads((grid_dir/'metadata.json').read_text(encoding='utf-8-sig'))
    fields = (grid_dir/'map.pgm').read_bytes().split(b'\n', 3)
    assert fields[0] == b'P5' and fields[2] == b'255'
    width, height = map(int,fields[1].split())
    pixels = fields[3]
    assert len(pixels) == width*height
    checks = []
    def check(name, passed, detail):
        checks.append({'name': name, 'passed': bool(passed), 'detail': detail})
    check('map_identity', runtime['map_id'] == data['id'] == motion['map_id'] == metadata['map_id'] and metadata['source_sha256'] == digest, digest)
    check('actual_movement', motion['status'] == 'passed' and motion['kinematic_probe_passed'] and len(motion['checks']) == 9 and all(c['passed'] for c in motion['checks']), 'nine actual engine checks, not just map load')
    actual = {b['id']:b for b in runtime['geometry']}
    error = 0.0
    for b in data['obstacles']:
        found = actual['obstacle_'+b['id']]
        x,y,z = b['center_m']; sx,sy,sz = b['size_m']; c,s = abs(math.cos(b['yaw_rad'])), abs(math.sin(b['yaw_rad']))
        center = [100*x,-100*y,100*z]; extent=[50*(c*sx+s*sy),50*(s*sx+c*sy),50*sz]
        for key,sign in (('bounds_min_cm',-1),('bounds_max_cm',1)):
            error = max(error, *(abs(found[key][i]-(center[i]+sign*extent[i])) for i in range(3)))
    check('obstacle_geometry', error < .005 and runtime['obstacle_count']==len(data['obstacles']), {'max_error_cm':error})
    spawn = next(s for s in data['spawns'] if s['id']==runtime['spawn_id'])
    pose = motion['checks'][0]['before']['actor']; xyz=pose['position_cm']; q=pose['rotation_xyzw']
    yaw = 2*math.atan2(q[2],q[3])
    check('spawn_transform', abs(xyz[0]-100*spawn['position_m'][0])<.001 and abs(xyz[1]+100*spawn['position_m'][1])<.001 and abs(yaw+spawn['yaw_rad'])<1.e-6 and not runtime['initial_overlap'], {'actual_cm':xyz,'actual_yaw_rad':yaw})
    expected_regions=[r['id'] for r in data['regions'] if all(abs(spawn['position_m'][i]-r['center_m'][i])<=r['size_m'][i]/2 for i in range(2))]
    check('semantic_regions', set(runtime['initial_regions'])==set(expected_regions) and runtime['region_count']==len(data['regions']), expected_regions)
    res=metadata['resolution']; origin=metadata['origin']
    def cell(x,y):
        col=int((x-origin[0])/res); row=height-1-int((y-origin[1])/res)
        return pixels[row*width+col]
    known = all(cell(b['center_m'][0],b['center_m'][1])==0 for b in data['obstacles'])
    known &= all(cell(s['position_m'][0],s['position_m'][1])==254 for s in data['spawns'])
    known &= cell(data['floor']['size_m'][0]/2-res/2,0)==0
    check('occupancy_landmarks', known, {'width':width,'height':height,'resolution':res,'origin':origin})
    result={'map_id':data['id'],'checks':checks,'passed':all(c['passed'] for c in checks),
        'scope':'native Windows flat geometry/collision/spawn/regions and static occupancy; not referee or localization'}
    (folder/'verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
