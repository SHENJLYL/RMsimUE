"""Reimport only two native school-gym mesh assets after verified CAD-footprint clipping.
Preserves every native material, original user SKP and all unrelated UE meshes.
"""
from pathlib import Path
import importlib.util,json,hashlib,traceback,unreal
root=Path(__file__).resolve().parents[2]
path=root/'tools/unreal/import_school_gym_assets.py'
spec=importlib.util.spec_from_file_location('school_native_import',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
inputs=module.argument('VenueAssetRoot')
output=module.argument('VenueImportReport')
if output.exists():raise FileExistsError('Report already exists')
manifest=json.loads((inputs/'prepared.json').read_text(encoding='utf-8-sig'))
full=root/'artifacts/fixes/downloaded-gym-20261119/school-native-import-02.json'
old=json.loads(full.read_text(encoding='utf-8-sig'))
# First editor import completed all 15 school assets but halted before the
# exterior because its quad-triangulated OBJ face manifest needed correction.
existing={entry['asset'].rsplit('/',1)[-1].split('.')[0] for entry in old['meshes']}
expected={entry['asset_name'] for entry in manifest['meshes'] if entry['asset_name'].startswith('RMSchool')}
if old['status'] not in ('failed','passed') or existing!=expected:
 raise RuntimeError('Existing school assets incomplete or changed; preserve and inspect')
mats={}
for name,data in manifest['materials'].items():
 if data.get('alpha_mode')=='BLEND' or data.get('semantic') in ('glass','window_glass'):
  p=module.CAMPUS+'/Materials/M_glass'
 else:
  p=module.BASE+'/Materials/M_'+name
 v=unreal.load_asset(p)
 if not isinstance(v,unreal.MaterialInterface):raise RuntimeError('Missing existing material '+p)
 mats[name]=v
modified=[];report={'status':'running','meshes':modified,'reused_materials':len(mats),
                      'prepared_sha256':module.sha(inputs/'prepared.json'),'source_import_report':str(full)}
try:
 for entry in manifest['meshes']:
  if entry['asset_name'] not in ('RMSchool_1_0','RMSchool_1_1','RMExteriorGround','RMExteriorBuildings','RMExteriorFurnishings','RMExteriorTree'):continue
  print('REBUILD_FIELD_CLEARED_SCHOOL_MESH',entry['asset_name'],flush=True)
  result=module.import_mesh(entry,inputs,manifest,mats)
  modified.append(result)
 if len(modified)!=6:raise RuntimeError('Expected two field-cleared school meshes and four CC0 exterior meshes')
 report['status']='passed'
except Exception as err:
 report.update(status='failed',error=str(err),traceback=traceback.format_exc())
 raise
finally:
 output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
unreal.log('RMSIM_SCHOOL_FIELD_MESH_PATCH_PASSED '+str(output))
