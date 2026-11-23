"""Assemble the source-faithful hall and full-detail tree into an immutable import bundle."""
from pathlib import Path
import json,hashlib,shutil,datetime
r=Path('D:/simulator')
school=r/'assets/maps/rmuc2027/arena/sketchupbox-school-gym'
source=school/'ue-import-04-source-fidelity'
tree=r/'assets/maps/rmuc2027/arena/campus-exterior/prepared-tree-fidelity-01'
out=school/'ue-import-05-fidelity'
if out.exists():raise FileExistsError(out)
shutil.copytree(source,out)
d=json.loads((source/'prepared.json').read_text(encoding='utf-8-sig'))
t=json.loads((tree/'prepared.json').read_text(encoding='utf-8-sig'))
assert t['status']=='passed' and t['expected_unique_surface_triangles']==1072212
for name in ('RMExteriorTree.obj','RMExteriorTree.mtl'):
 shutil.copy2(tree/name,out/name)
for p in (tree/'textures').iterdir():
 if p.is_file():shutil.copy2(p,out/'textures'/p.name)
shutil.copy2(tree/'prepared.json',out/'tree-fidelity-source.json')
shutil.copy2(tree/'SOURCE-textures.json',out/'tree-textures-source.json')
print('TREE_KEYS',list(t))
entry=t['meshes']['RMExteriorTree']
if entry is None:
 for k,v in t.items():
  if isinstance(v,dict) and v.get('asset_name')=='RMExteriorTree':entry=v;break
if entry is None:raise ValueError('No tree mesh manifest found')
assert entry['triangles']==1072212
d['meshes']=[entry if x['asset_name']=='RMExteriorTree' else x for x in d['meshes']]
d['materials'].update(t['materials'])
d['schema']='rmsim_school_gym_native_fidelity_v1'
d['source_skp_sha256']='a40bbf0c69bace3b5ee1aed3795217e69c88c4c58ce71b191aff3e033e3502bc'
d['source_sha256']=d['source_skp_sha256']
d['restored_glb_sha256']=d.pop('source_file_sha256')
d['source_file']='../source/Indoor Basketball Gym.skp'
d['tree_policy']={'decimation':False,'source_triangles':t['source_triangles'],'proven_reverse_duplicate_count_removed':1,'prepared_triangles':1072212,'provenance':'tree-fidelity-source.json','textures':'tree-textures-source.json'}
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
d['files']={}
for p in sorted(out.rglob('*')):
 if p.is_file() and p.name!='prepared.json':
  d['files'][p.relative_to(out).as_posix()]={'bytes':p.stat().st_size,'sha256':sha(p)}
for m in d['meshes']:
 assert m['sha256']==sha(out/m['file']),(m['asset_name'],'sha mismatch')
 slots=set(m['material_slots'])
 assert slots<=set(d['materials'])
d['assembled_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
(out/'prepared.json').write_text(json.dumps(d,indent=2)+'\n',encoding='utf-8')
summary={'status':'assembled_pending_native_import','directory':str(out),'prepared_sha256':sha(out/'prepared.json'),'meshes':len(d['meshes']),'materials':len(d['materials']),'triangles':sum(x['triangles'] for x in d['meshes']),'source_skp_sha256':d['source_skp_sha256']}
log=r/'artifacts/fixes/school-view-fidelity-20261119/bundle-01';log.mkdir(exist_ok=False)
(log/'report.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
print(json.dumps(summary,indent=2))
