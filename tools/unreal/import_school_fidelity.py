"""Import restored school assets into a new namespace; activation follows independent QA."""
from pathlib import Path
import importlib.util,hashlib,json,traceback
import unreal
ROOT=Path(__file__).resolve().parents[2]
def module(name,relative):
 spec=importlib.util.spec_from_file_location(name,ROOT/relative)
 value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
I=module('school_legacy_transport','tools/unreal/import_school_gym_assets.py')
F=module('school_fidelity_materials','tools/unreal/school_fidelity_materials.py')
I.BASE=F.BASE
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 root=I.argument('VenueAssetRoot');output=I.argument('VenueImportReport')
 if output.exists():raise FileExistsError(output)
 output.parent.mkdir(parents=True,exist_ok=True)
 manifest=json.loads((root/'prepared.json').read_text(encoding='utf-8-sig'))
 if manifest['source_skp_sha256']!='a40bbf0c69bace3b5ee1aed3795217e69c88c4c58ce71b191aff3e033e3502bc':raise ValueError('Unexpected source SKP')
 if len(manifest['meshes'])!=23:raise ValueError('Expected exact 23 prepared assets')
 report={'status':'running','prepared_sha256':sha(root/'prepared.json'),'asset_root':str(root),'native_base':F.BASE,'materials':[],'textures':[],'meshes':[],'scope':'Native authoring only. Source/native face verification and rendered inspection remain separate gates.'}
 def save():output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
 try:
  cache={};materials={}
  for name,data in manifest['materials'].items():
   unreal.log('RMSIM_FIDELITY_MATERIAL '+name)
   materials[name]=F.make_material(name,data,root,manifest,cache,report)
  report['presentation_assets']=F.import_presentation_assets(report=report)
  save()
  for entry in manifest['meshes']:
   unreal.log('RMSIM_FIDELITY_MESH_START '+entry['asset_name'])
   row=I.import_mesh(entry,root,manifest,materials)
   row['geometry_gate']='pending_independent_per_face_audit'
   report['meshes'].append(row);save()
   unreal.log('RMSIM_FIDELITY_MESH_IMPORTED '+entry['asset_name'])
  unreal.SystemLibrary.execute_console_command(None,'Editor.AsyncAssetCompilationFinishAll')
  for record in report['textures']:
   value=unreal.load_asset(record['asset'])
   if hasattr(value,'blueprint_get_size_x'):
    record['native_size_after_compilation']=[value.blueprint_get_size_x(),value.blueprint_get_size_y()]
   if not unreal.EditorAssetLibrary.save_loaded_asset(value):raise RuntimeError('Texture final save failed')
  report['status']='imported_pending_independent_geometry_and_visual_qa'
 except Exception as error:
  report.update(status='failed',error=str(error),traceback=traceback.format_exc())
  unreal.log_error('RMSIM_FIDELITY_IMPORT_FAILED '+str(error))
  raise
 finally:
  directory=Path(unreal.Paths.project_content_dir()).resolve()/'RMSim/Maps/rmuc2027/Arena/SchoolGymFidelity'
  report['script_sha256']=sha(Path(__file__))
  report['materials_helper_sha256']=sha(ROOT/'tools/unreal/school_fidelity_materials.py')
  report['native_files']={p.relative_to(directory).as_posix():{'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(directory.rglob('*.uasset'))}
  save()
 unreal.log('RMSIM_FIDELITY_IMPORT_COMPLETE '+str(output))
if __name__=='__main__':main()
