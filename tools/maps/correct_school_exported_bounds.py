"""Correct derived AABBs from exported vertices; preserve all OBJ bytes."""
from pathlib import Path
import json,hashlib,datetime
r=Path('D:/simulator');bundle=r/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/ue-import-05-fidelity'
p=bundle/'prepared.json';before=p.read_bytes();d=json.loads(before)
out=r/'artifacts/fixes/school-view-fidelity-20261119/exported-bounds-01';out.mkdir(exist_ok=False)
(out/'prepared.before.json').write_bytes(before)
changes=[]
for e in d['meshes']:
 lo=[float('inf')]*3;hi=[float('-inf')]*3;count=0
 with (bundle/e['file']).open(encoding='utf-8') as f:
  for line in f:
   if line.startswith('v '):
    xyz=[float(x) for x in line.split()[1:4]];count+=1
    for k in range(3):lo[k]=min(lo[k],xyz[k]);hi[k]=max(hi[k],xyz[k])
 assert count>0
 old=[e['bounds_min_m'],e['bounds_max_m']]
 if lo!=old[0] or hi!=old[1]:changes.append({'mesh':e['asset_name'],'prior_rotated_object_bounds':old,'actual_exported_obj_bounds':[lo,hi]})
 e['bounds_min_m']=lo;e['bounds_max_m']=hi
d['bounds_policy']='Exact exported OBJ vertex bounds; layout uses final native bounds to retain identity scale. Original transformed component bounding boxes remain in archived preparation metadata.'
p.write_text(json.dumps(d,indent=2)+'\n',encoding='utf-8')
summary={'status':'passed','geometry_bytes_unchanged':True,'prepared_sha256_before':hashlib.sha256(before).hexdigest(),'prepared_sha256_after':hashlib.sha256(p.read_bytes()).hexdigest(),'changes':changes}
(out/'report.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
print('BOUNDS_METADATA_CORRECTED',len(changes),summary['prepared_sha256_after'])
