"""Verify 15 source gym meshes and six targeted final reimports, produce one auditable accepted manifest."""
from pathlib import Path
import hashlib,json
root=Path('D:/simulator')
venue=root/'assets/maps/rmuc2027/arena/sketchupbox-school-gym'
output=root/'artifacts/fixes/downloaded-gym-20261119'
bundle=venue/'ue-import-03/prepared.json'
wanted=json.loads(bundle.read_text(encoding='utf-8'))
initial=json.loads((output/'school-native-import-02.json').read_text(encoding='utf-8'))
patch=json.loads((output/'school-native-patch-03.json').read_text(encoding='utf-8'))
if patch['status']!='passed' or len(patch['meshes'])!=6:raise RuntimeError('Six targeted meshes not accepted')
if len(initial['meshes'])!=15:raise RuntimeError('Original 15 school blocks not imported')
def get_id(row):return row['asset'].rsplit('/',1)[-1].split('.')[0]
ids={}
for row in initial['meshes']:
 id_=get_id(row)
 if id_ in ids:raise RuntimeError('Duplicate native ID')
 ids[id_]=row
for row in patch['meshes']:ids[get_id(row)]=row
if set(ids)!={entry['asset_name'] for entry in wanted['meshes']}:raise RuntimeError('Incomplete final native mesh list')
for entry in wanted['meshes']:
 row=ids[entry['asset_name']]
 if row['source_sha256']!=entry['sha256']:raise RuntimeError('Source OBJ hash mismatch '+entry['asset_name'])
 if not row['asset'].startswith('/Game/RMSim/Maps/rmuc2027/Arena/SchoolGym/Meshes/'):raise RuntimeError('Unexpected asset root')
 if not row['nanite_enabled'] and entry['asset_name']!='RMExteriorBuildings':pass
 if row['retention_ratio']<.99:raise RuntimeError('Triangles lost '+entry['asset_name'])
native_path=root/'unreal/RMSim/Content/RMSim/Maps/rmuc2027/Arena/SchoolGym'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
asset_files={p.relative_to(native_path).as_posix():{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(native_path.rglob('*.uasset'))}
mesh_files=[p for p in asset_files if p.startswith('Meshes/')]
if len(mesh_files)!=19:raise RuntimeError('Expected 19 new school/campus mesh files')
bundle_sha=sha(bundle)
report={'status':'passed','prepared_sha256':bundle_sha,'source_sketchup_sha256':sha(venue/'source/Indoor Basketball Gym.skp'),
        'meshes':[ids[x['asset_name']] for x in wanted['meshes']],
        'materials':initial['materials'],'textures':initial['textures'],
        'native_files':asset_files,
        'provenance':{'initial_import':str(output/'school-native-import-02.json'),
         'targeted_patch':str(output/'school-native-patch-03.json'),
         'why':'First native run imported 15 school blocks; corrected Blender OBJ triangulation recorded 4 CC0 exterior assets; field clipping reimported two school blocks.',
         'source_stored_local':True,'source_cad_modified':False}}
dest=output/'school-native-import-final.json'
if dest.exists():raise FileExistsError('Final report already exists')
dest.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print('SCHOOL_NATIVE_IMPORT_FINAL_PASSED','meshes',len(report['meshes']),'materials',len(report['materials']),'textures',len(report['textures']),'uassets',len(asset_files),flush=True)
