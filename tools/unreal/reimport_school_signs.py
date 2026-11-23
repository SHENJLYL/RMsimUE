"""Reimport only flipped generated school sign texture copies; official source unchanged."""
from pathlib import Path
import json,unreal,hashlib,traceback
ROOT=Path('D:/simulator')
SOURCE=ROOT/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/ue-import-03/textures'
OUT=ROOT/'artifacts/fixes/downloaded-gym-20261119/school-sign-orientation-01'
FILE=OUT/'ue-reimport-01.json'
if FILE.exists():raise FileExistsError('UE reimport already recorded')
report={'status':'running','textures':[]}
try:
 for name in ('RMBrandLongNorth','RMBrandLongSouth','RMBrandShortEast','RMBrandShortWest'):
  path=SOURCE/(name+'.png')
  asset='/Game/RMSim/Maps/rmuc2027/Arena/SchoolGym/Textures/T_'+name+'_Color'
  target=unreal.load_asset(asset)
  if not isinstance(target,unreal.Texture2D):raise RuntimeError('Missing intended texture '+asset)
  task=unreal.AssetImportTask()
  for key,value in {'filename':str(path),'destination_path':'/Game/RMSim/Maps/rmuc2027/Arena/SchoolGym/Textures',
                     'destination_name':'T_'+name+'_Color','automated':True,'save':True,
                     'replace_existing':True,'replace_existing_settings':True}.items():
   task.set_editor_property(key,value)
  unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
  if not unreal.EditorAssetLibrary.does_asset_exist(asset):raise RuntimeError('Texture missing after import '+asset)
  loaded=unreal.load_asset(asset)
  if not unreal.EditorAssetLibrary.save_loaded_asset(loaded):raise RuntimeError('Texture save failed')
  report['textures'].append({'asset':asset,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
 report['status']='passed'
except Exception as e:
 report.update(status='failed',error=str(e),traceback=traceback.format_exc());raise
finally:
 FILE.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
unreal.log('RMSIM_SCHOOL_SIGN_REIMPORT_PASS '+str(FILE))
