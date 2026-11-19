"""Resume only unfinished assets; require explicit full fallback geometry."""
from pathlib import Path
import importlib.util,hashlib,json,traceback
import unreal
ROOT=Path(__file__).resolve().parents[2]
def module(n,p):
 s=importlib.util.spec_from_file_location(n,ROOT/p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
I=module('school_resume_transport','tools/unreal/import_school_gym_assets.py')
F=module('school_resume_materials','tools/unreal/school_fidelity_materials.py')
I.BASE=F.BASE
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def bounds_row(mesh):
 b=mesh.get_bounds();c=[b.origin.x,b.origin.y,b.origin.z];e=[b.box_extent.x,b.box_extent.y,b.box_extent.z]
 return {'bounds_min_cm':[c[i]-e[i] for i in range(3)],'bounds_max_cm':[c[i]+e[i] for i in range(3)]}
def main():
 root=I.argument('VenueAssetRoot');out=I.argument('VenueImportReport')
 previous=ROOT/'artifacts/fixes/school-view-fidelity-20261119/native-import-01/report.json'
 old=json.loads(previous.read_text(encoding='utf-8-sig'));d=json.loads((root/'prepared.json').read_text(encoding='utf-8-sig'))
 if len(old['meshes'])!=8 or old['status']!='failed':raise ValueError('Unexpected resume checkpoint')
 if out.exists():raise FileExistsError(out)
 out.parent.mkdir(parents=True,exist_ok=True)
 report={'status':'running','prepared_sha256':sha(root/'prepared.json'),'asset_root':str(root),'native_base':F.BASE,'materials':old['materials'],'textures':old['textures'],'meshes':[],'presentation_assets':old['presentation_assets'],'presentation_details':old['presentation_details'],'resume_from':str(previous),'resume_report_sha256':sha(previous),'scope':'Explicit full Nanite fallback authoring. Independent per-face and visual verification still required.'}
 def save():out.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
 try:
  materials={x['slot']:unreal.load_asset(x['asset']) for x in old['materials']}
  if set(materials)!=set(d['materials']) or any(x is None for x in materials.values()):raise ValueError('Saved source materials incomplete')
  reused={m['asset'].rsplit('/',1)[-1].split('.')[0]:m for m in old['meshes']}
  for entry in d['meshes']:
   name=entry['asset_name'];unreal.log('RMSIM_FIDELITY_RESUME_START '+name)
   if name in reused:
    row=dict(reused[name])
    if sha(root/entry['file'])!=row['source_sha256']:raise ValueError('Saved source geometry changed')
    mesh=unreal.load_asset(row['asset']);settings=I.SMS.get_nanite_settings(mesh)
    settings.set_editor_property('fallback_target',unreal.NaniteFallbackTarget.PERCENT_TRIANGLES)
    settings.set_editor_property('fallback_percent_triangles',1.)
    settings.set_editor_property('fallback_relative_error',0.)
    settings.set_editor_property('keep_percent_triangles',1.)
    settings.set_editor_property('trim_relative_error',0.)
    I.SMS.set_nanite_settings(mesh,settings,True)
    if not I.EAL.save_loaded_asset(mesh):raise RuntimeError('Cannot save full fallback')
    row.update(bounds_row(mesh));row['reused_geometry']=True
   else:
    row=I.import_mesh(entry,root,d,materials,full_fallback=True)
    mesh=unreal.load_asset(row['asset']);row['reused_geometry']=False
   row['fallback_target']=str(I.SMS.get_nanite_settings(mesh).get_editor_property('fallback_target'))
   row['fallback_percent_triangles']=float(I.SMS.get_nanite_settings(mesh).get_editor_property('fallback_percent_triangles'))
   row['geometry_gate']='pending_independent_per_face_audit'
   report['meshes'].append(row);save()
   unreal.log('RMSIM_FIDELITY_RESUME_IMPORTED '+name)
  world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
  unreal.SystemLibrary.execute_console_command(world,'Editor.AsyncAssetCompilationFinishAll')
  for row in report['meshes']:
   mesh=unreal.load_asset(row['asset']);row['full_fallback_triangles_after_compilation']=int(mesh.get_num_triangles(0));row.update(bounds_row(mesh));I.EAL.save_loaded_asset(mesh)
  for record in report['textures']:
   value=unreal.load_asset(record['asset'])
   if hasattr(value,'blueprint_get_size_x'):record['native_size_after_compilation']=[value.blueprint_get_size_x(),value.blueprint_get_size_y()]
   I.EAL.save_loaded_asset(value)
  report['status']='imported_pending_independent_geometry_and_visual_qa'
 except Exception as e:
  report.update(status='failed',error=str(e),traceback=traceback.format_exc());unreal.log_error(str(e));raise
 finally:
  folder=Path(unreal.Paths.project_content_dir()).resolve()/'RMSim/Maps/rmuc2027/Arena/SchoolGymFidelity'
  report['native_files']={p.relative_to(folder).as_posix():{'bytes':p.stat().st_size,'sha256':sha(p)} for p in folder.rglob('*.uasset')}
  report['script_sha256']=sha(Path(__file__));report['mesh_importer_sha256']=sha(ROOT/'tools/unreal/import_school_gym_assets.py');save()
 unreal.log('RMSIM_FIDELITY_RESUME_COMPLETE')
if __name__=='__main__':main()
