"""Remove ONLY authored SketchUp visual triangles inside the official field.
Keeps the source SKP, all vertices/UV/material records, field CAD and physics.
Write replacement OBJ copies for two sports-hall blocks; source originals untouched.
"""
from pathlib import Path
import hashlib,json,shutil
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
root=Path('D:/simulator')
venue=root/'assets/maps/rmuc2027/arena/sketchupbox-school-gym'
base=venue/'ue-import-02'
out=venue/'ue-import-03'
if out.exists():raise FileExistsError('Refuse to overwrite combined import batch')
shutil.copytree(base,out,ignore=shutil.ignore_patterns('fbx_transport'))
manifest=json.loads((out/'prepared.json').read_text(encoding='utf-8'))
report={}
for stem in ['RMSchool_1_0','RMSchool_1_1']:
 p=out/(stem+'.obj')
 verts=[None];before=0;removed=0
 # Exact face centre in the source CAD footprint; leave 2cm margin to trim.
 # Horizontal full 3D geometry above 4.5m (lighting/truss) is untouched.
 new_lines=[]
 for line in p.open('r',encoding='utf-8',errors='replace'):
  if line.startswith('v '):
   verts.append(tuple(float(v) for v in line.split()[1:4]))
  if line.startswith('f '):
   before+=1
   cs=line.split()[1:]
   points=[verts[int(x.split('/')[0])] for x in cs]
   cen=[sum(v[i] for v in points)/len(points) for i in range(3)]
   if abs(cen[0])<13.98 and abs(cen[1])<7.98 and -.27<cen[2]<4.5:
    removed+=1;continue
  new_lines.append(line)
 if removed<10000:raise RuntimeError('Expected overlapping authored school surface '+stem)
 p.write_text(''.join(new_lines),encoding='utf-8',newline='\n')
 record=next(x for x in manifest['meshes'] if x['asset_name']==stem)
 record['sha256']=sha(p)
 record['bytes']=p.stat().st_size
 record['triangles']=before-removed
 manifest['files'][p.name]={'bytes':p.stat().st_size,'sha256':sha(p)}
 report[stem]={'before_faces':before,'removed_field_intrusion_faces':removed,'retained_faces':before-removed,'source_fbx_no_change':True}
(out/'prepared.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
print('FIELD_OVERLAP_PATCH_PREPARED',json.dumps(report),flush=True)
(root/'artifacts/fixes/downloaded-gym-20261119/school-field-clearance-01.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
