"""Independent per-face verification of saved SchoolGymFidelity native assets."""
from pathlib import Path
import importlib.util,hashlib,json,traceback,time
import unreal
ROOT=Path(__file__).resolve().parents[2]
def module(name,path):
 s=importlib.util.spec_from_file_location(name,ROOT/path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
I=module('fidelity_ue_helpers','tools/unreal/import_school_gym_assets.py')
G=module('fidelity_strict_geometry','tools/unreal/verify_school_fidelity_geometry.py')
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 root=I.argument('VenueAssetRoot');out=I.argument('VenueImportReport')
 previous=I.argument('FidelityImportedReport')
 imported=json.loads(previous.read_text(encoding='utf-8-sig'))
 d=json.loads((root/'prepared.json').read_text(encoding='utf-8-sig'))
 if imported['status']!='imported_pending_independent_geometry_and_visual_qa':raise ValueError('Native import incomplete')
 if imported['prepared_sha256']!=sha(root/'prepared.json'):raise ValueError('Prepared identity changed')
 if out.exists():raise FileExistsError(out)
 out.parent.mkdir(parents=True,exist_ok=True)
 report={'status':'running','prepared_sha256':sha(root/'prepared.json'),'import_report':str(previous),'import_report_sha256':sha(previous),'meshes':[],'scope':'Independent reload of saved actual render LOD0; per-face position/UV0/material/orientation matching. Visual appearance is separately reviewed.'}
 def save():out.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
 try:
  world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
  unreal.SystemLibrary.execute_console_command(world,'Editor.AsyncAssetCompilationFinishAll')
  by_name={m['asset'].rsplit('/',1)[-1].split('.')[0]:m for m in imported['meshes']}
  assert len(by_name)==len(d['meshes'])==23
  for entry in d['meshes']:
   name=entry['asset_name'];rec=by_name[name]
   mesh=unreal.load_asset(rec['asset'])
   if not isinstance(mesh,unreal.StaticMesh):raise ValueError('Native mesh missing '+name)
   if sha(root/entry['file'])!=entry['sha256']:raise ValueError('Source OBJ changed '+name)
   unreal.log('RMSIM_STRICT_GEOMETRY_START '+name);t=time.monotonic()
   detail=out.parent/'geometry'/f'{name}.json'
   result=G.verify_static_mesh_against_obj(mesh,root/entry['file'],expected_source_triangles=entry['triangles'],report_path=detail,raise_on_failure=False)
   row={k:v for k,v in result.items() if k in ('status','source_triangles','native_render_triangles','matched_triangles','proven_absent_degenerate_or_duplicate_triangles','unknown_missing_triangles','unexpected_native_triangles','scalar_rounding_match_triangles','max_matched_scalar_ulp')}
   row.update(asset=rec['asset'],details=str(detail),details_sha256=sha(detail),seconds=time.monotonic()-t)
   report['meshes'].append(row);save()
   unreal.log('RMSIM_STRICT_GEOMETRY_RESULT '+name+' '+result['status'])
  report['status']='passed' if all(row['status']=='passed' for row in report['meshes']) else 'failed'
  if report['status']=='failed':report['error']='One or more source/native comparisons failed; inspect every per-mesh report.'
  report['totals']={k:sum(m[k] for m in report['meshes']) for k in ['source_triangles','native_render_triangles','matched_triangles','proven_absent_degenerate_or_duplicate_triangles','unknown_missing_triangles','unexpected_native_triangles']}
 except Exception as e:
  report.update(status='failed',error=str(e),traceback=traceback.format_exc());raise
 finally:
  report['checker_sha256']=sha(ROOT/'tools/unreal/verify_school_fidelity_geometry.py');report['script_sha256']=sha(Path(__file__));save()
 unreal.log('RMSIM_STRICT_GEOMETRY_COMPLETE')
if __name__=='__main__':main()
