"""Remove the added spectator modules and preserve the stable GPU profile."""
from pathlib import Path
import json,hashlib,shutil,datetime
root=Path('D:/simulator')
out=root/'artifacts/fixes/school-view-fidelity-20261119/layout-01'
out.mkdir(parents=True,exist_ok=False)
rel='assets/maps/rmuc2027/arena/sketchupbox-school-gym/layout.json'
p=root/rel
before=p.read_bytes()
d=json.loads(before)
assert d['source']['source_file_sha256']=='a40bbf0c69bace3b5ee1aed3795217e69c88c4c58ce71b191aff3e033e3502bc'
assert len(d['native_meshes'])==20
removed=[g for g in d['groups'] if g['id'] in ('RMSchoolRealSeats','RMSchoolConcreteTiers')]
assert {g['id'] for g in removed}=={'RMSchoolRealSeats','RMSchoolConcreteTiers'}
d['groups']=[g for g in d['groups'] if g not in removed]
del d['native_meshes']['RMSchoolSeats']
d.pop('seat_attribution',None)
d['field_clearance_note']='Added spectator chairs and concrete tiers removed at user request. Original source-model benches remain part of the school building.'
assert len(d['native_meshes'])==19 and len(d['groups'])==19
assert sum(len(g['instances']) for g in d['groups'])==49
assert all(l['cast_shadows'] is False for l in d['lights'])
(out/'layout.before.json').write_bytes(before)
cfg=root/'unreal/RMSim/Config/Custom/RMSimGymLumen/DefaultEngine.ini'
cfg_before=cfg.read_bytes()
(out/'DefaultEngine.before.ini').write_bytes(cfg_before)
assert 'r.Shadow.Virtual.Enable=0' in cfg_before.decode('utf-8-sig')
new=json.dumps(d,ensure_ascii=False,indent=2)+'\n'
assert p.read_bytes()==before
p.write_text(new,encoding='utf-8')
capture=root/'tools/unreal/capture_school_gym.py'
cap=capture.read_text(encoding='utf-8-sig')
(out/'capture_school_gym.before.py').write_text(cap,encoding='utf-8')
assert cap.count("probe['native_mesh_assets_loaded']==20,probe['native_mesh_instances']==193")==1
cap=cap.replace("probe['native_mesh_assets_loaded']==20,probe['native_mesh_instances']==193","probe['native_mesh_assets_loaded']==19,probe['native_mesh_instances']==49")
anchor=" 'OppositeView':{'position':[19,14,7.9],'target':[0,-4,3],'fov':75}"
assert anchor in cap
cap=cap.replace(anchor,anchor+",\n 'FieldWindow':{'position':[-5,0,1.25],'target':[-5,28,6.0],'fov':78},\n 'FieldEndWindow':{'position':[-8,-4,1.3],'target':[-35,0,6.0],'fov':75},\n 'BoundaryClose':{'position':[8,6,1.6],'target':[8,8.001566,0.0],'fov':65},\n 'BoundaryCorner':{'position':[11,5.5,2.0],'target':[14,8,0.2],'fov':75}")
compile(cap,str(capture),'exec')
capture.write_text(cap,encoding='utf-8')
sha=lambda b:hashlib.sha256(b).hexdigest()
report={'status':'applied_pending_visual_qa','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'removed_groups':[{k:v for k,v in g.items() if k!='instances'}|{'instance_count':len(g['instances'])} for g in removed],'added_seat_count_removed':864,'added_tier_modules_removed':72,'source_model_untouched':True,'layout_sha256_before':sha(before),'layout_sha256_after':sha(p.read_bytes()),'native_meshes':19,'native_instances':49,'vsm_disabled_preserved':True,'all_rect_shadows_disabled_preserved':True,'gpu_config_unchanged':cfg.read_bytes()==cfg_before}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,indent=2))
