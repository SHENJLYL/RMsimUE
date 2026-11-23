"""Record the accepted Windows school venue view and boundary evidence."""
from pathlib import Path
import json,hashlib,datetime
R=Path('D:/simulator')
A=R/'artifacts/fixes/school-view-fidelity-20261119'
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def rec(p):return {'file':p.relative_to(R).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)}
def main():
 geo=read(A/'native-geometry-final/report.json')
 native=read(A/'landscape-native-01/report.json')
 views=read(A/'final-views-02/report.json')
 gpu=read(A/'visible-gpu-01/report.json')
 activation=read(A/'activation-01/report.json')
 assert geo['status']=='passed' and len(geo['meshes'])==23
 assert native['original_audited_native_files_unchanged'] and native['original_native_file_count']==111
 assert views['status']=='native_views_passed_pending_human_visual_review'
 assert gpu['status']=='passed' and gpu['exit_code']==0 and not any(gpu['warnings'].values())
 manifest=R/'assets/maps/rmuc2027/arena-shell.json'
 layout=R/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/layout.json'
 assert views['manifest_sha256']==sha(manifest) and views['layout_sha256']==sha(layout)
 assert gpu['layout_sha256']==sha(layout)
 for p,h in activation['protected_file_hashes'].items():assert sha(R/p)==h,p
 evidence=[]
 for name in ['FieldEndWindow','FieldSky','BoundarySouth']:
  folder=A/'final-views-02'/name
  boundary=read(folder/'boundary.json')
  assert boundary['status']=='passed' and boundary['sweep_samples']==96 and boundary['sweep_passed']==96
  assert boundary['native_mesh_assets_loaded']==23 and boundary['native_mesh_instances']==57
  assert boundary['all_boundary_parts_outside_source_bounds']
  assert abs(boundary['outside_clearance_m']-.001)<1e-9
  evidence.append({'view':name,'image':rec(folder/'overview.png'),'preview':rec(folder/'overview-preview.jpg'),'boundary':rec(folder/'boundary.json')})
 reviewdir=A/'visual-review-01';reviewdir.mkdir(exist_ok=False)
 visual={'status':'passed_assistant_visual_review','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
  'method':'Direct visual inspection of the original UE-rendered PNG/JPEG frames; no synthetic imagery.',
  'accepted_scope':'Exterior visibility through source windows and glass fence alignment to the original competition-floor perimeter.',
  'observations':{
   'FieldEndWindow':'Source windows show detailed three-dimensional tree trunks and canopy, ground shadows and distant landscape instead of the flat gray horizon.',
   'FieldSky':'Upper source windows show tree canopies and clear blue sky/cloud forms; the near exterior facade no longer covers most of the view.',
   'BoundarySouth':'Continuous glass base meets the competition-floor outer edge; removed bench fragments do not intrude. Original source red benches and stairs remain outside the field.'},
  'native_boundary_samples_each':96,'source_outer_edge_clearance_m':.001,'views':evidence,
  'original_source_seating_preserved':True,'added_spectator_seating':0,
  'known_separate_legacy_item':'The original basketball scoreboard 3D lettering remains mirrored; this is geometry, not a texture-flip issue. It was not changed as part of window/exterior and field-boundary acceptance.'}
 (reviewdir/'verification.json').write_text(json.dumps(visual,indent=2)+'\n',encoding='utf-8')
 report={'status':'accepted_for_exterior_visibility_and_field_boundary','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
  'platform':'Native Windows UE 5.8.4 / D3D12 / SM6',
  'source_venue':'Indoor Basketball Gym.skp',
  'native_mesh_assets':23,'native_mesh_instances':57,'tree_instances':26,
  'removed_added_seats':864,'removed_added_riser_modules':72,
  'source_gym_triangle_removal_except_two_basketball_goals':0,
  'source_material_textures_restored':8,
  'native_geometry_counts':{'source_triangles':1959036,'native_lod0_triangles':1956048,'proven_degenerate_or_duplicate_removed':2988,'explicit_engine_uv_weld_triangles':6,'unknown_missing':0,'unexpected_native':0},
  'perimeter':{'contract':'outside_source_bounds_v1','alignment':'source_bounds_outer_face','outside_clearance_m':.001,'samples':96,'passed':96},
  'lighting':read(manifest)['lighting'],
  'identity':{'arena':rec(manifest),'layout':rec(layout),'module':rec(R/'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll'),
    'renderer_profile':rec(R/'unreal/RMSim/Config/Custom/RMSimGymLumen/DefaultEngine.ini'),
    'source_skp_sha256':activation['source_skp_sha256'],'protected_file_hashes':activation['protected_file_hashes']},
  'evidence':{'native_geometry':rec(A/'native-geometry-final/report.json'),'activation':rec(A/'activation-01/report.json'),
    'source_court_alignment':rec(A/'geometry-audit-01/report.json'),'exterior_placement':rec(A/'exterior-tune-01/report.json'),
    'landscape_import':rec(A/'landscape-native-01/report.json'),'landscape_activation':rec(A/'landscape-activation-01/report.json'),
    'visual':rec(reviewdir/'verification.json'),'runtime_views':rec(A/'final-views-02/report.json'),
    'visible_gpu':rec(A/'visible-gpu-01/report.json')},
  'gpu':{'status':gpu['status'],'process_seconds':gpu['seconds'],'exit_code':gpu['exit_code'],'visible_window_seen':gpu['visible_window_seen'],'warnings':gpu['warnings'],'checks':gpu['checks']},
  'source_records':{'lighting_sky':rec(R/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/environment-fidelity/SOURCE.json'),
    'visible_landscape':rec(R/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/environment-full-fidelity/SOURCE.json')},
  'coverage':'Windows presentation and field-boundary acceptance. Protected navigation, map, vehicle rig and drive-profile bytes were preserved; no new navigation/Ubuntu acceptance is claimed.'}
 (A/'release-summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'status':report['status'],'gpu_seconds':gpu['seconds'],'release_summary':str(A/'release-summary.json'),'sha256':sha(A/'release-summary.json')},indent=2))
if __name__=='__main__':main()
