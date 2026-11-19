"""Validate the first saved source-fidelity mesh before resuming the import."""
from pathlib import Path
import importlib.util,json
import unreal
ROOT=Path(__file__).resolve().parents[2]
def module(name,path):
 s=importlib.util.spec_from_file_location(name,ROOT/path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
I=module('i','tools/unreal/import_school_gym_assets.py');G=module('g','tools/unreal/verify_school_fidelity_geometry.py')
root=I.argument('VenueAssetRoot');output=I.argument('VenueImportReport')
d=json.loads((root/'prepared.json').read_text(encoding='utf-8-sig'))
entry=d['meshes'][0]
mesh=unreal.load_asset('/Game/RMSim/Maps/rmuc2027/Arena/SchoolGymFidelity/Meshes/'+entry['asset_name'])
result=G.verify_static_mesh_against_obj(mesh,root/entry['file'],expected_source_triangles=entry['triangles'],report_path=output,raise_on_failure=False)
unreal.log('RMSIM_GEOMETRY_DIAGNOSTIC '+json.dumps({k:v for k,v in result.items() if k not in ('missing_source_faces','per_face_exceptions','unmatched_native_faces')}))
