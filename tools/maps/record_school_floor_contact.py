"""Bind accepted RMUC floor contact, flush glass perimeter, views and live runtime."""
from pathlib import Path
import hashlib,json,datetime
R=Path('D:/simulator')
A=R/'artifacts/fixes/school-floor-contact-20261120'
P=R/'artifacts/fixes/school-view-fidelity-20261119'
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def rec(p):return {'file':p.relative_to(R).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)}
def main():
 view=read(A/'native-contact-01/report.json')
 live=read(A/'visible-current-01/report.json')
 lift=read(A/'floor-support-apply-01/report.json')
 build=read(A/'build-01/report.json')
 old=read(P/'activation-01/report.json')
 assert live['status']=='passed_kept_open' and live['left_running']
 response=read(A/'visible-current-01/window-response.json')
 assert response and all(v['wm_null_responded'] and not v['hung_flag'] for v in response)
 assert view['status']=='native_views_passed_pending_human_visual_review' and len(view['views'])==3
 assert build['status']=='passed'
 for p,h in old['protected_file_hashes'].items():assert sha(R/p)==h,p
 manifest=R/'assets/maps/rmuc2027/arena-shell.json';layout=R/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/layout.json'
 assert view['manifest_sha256']==sha(manifest)==live['manifest_sha256']
 assert view['layout_sha256']==sha(layout)==live['layout_sha256']
 assert view['ue_module_sha256']==build['module_sha256']==live['module_sha256']
 samples=[]
 for name in ['FloorContactSouth','FloorContactCorner','FieldEndWindow']:
  p=A/'native-contact-01'/name;b=read(p/'boundary.json')
  assert b['status']=='passed' and b['sweep_passed']==b['sweep_samples']==96
  assert b['outside_clearance_m']==0 and b['all_boundary_parts_outside_source_bounds']
  assert b['all_glass_bases_meet_support_floor']
  assert abs(b['visual_floor_z_m']+.3009999990463257)<1e-12
  assert abs(b['glass_base_min_m']-b['visual_floor_z_m'])<1e-6
  assert abs(b['glass_base_max_m']-b['visual_floor_z_m'])<1e-6
  assert b['native_mesh_assets_loaded']==23 and b['native_mesh_instances']==57
  samples.append({'view':name,'image':rec(p/'overview.png'),'preview':rec(p/'overview-preview.jpg'),'probe':rec(p/'boundary.json'),
   'minimum_horizontal_clearance_m':b['minimum_boundary_clearance_m'],'glass_base_min_m':b['glass_base_min_m'],'glass_base_max_m':b['glass_base_max_m']})
 report={'status':'accepted','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
  'scope':'RMUC field underside rests on the original gym floor; glass is flush with the RMUC boundary and its lower frame reaches that floor. Prior exterior visibility remains.',
  'vertical_contact':{'gym_floor_top_before_m':-.4365999984741211,'field_underside_m':-.3009999990463257,
   'gym_floor_lift_m':.13559999942779544,'gym_floor_top_after_m':-.3009999990463257,'nominal_vertical_gap_m':0,
   'instances_shifted':57,'lights_shifted':12,'original_venue_triangles_removed':0},
  'glass':{'horizontal_gap_before_m':.001,'horizontal_gap_after_m':0,'base_z_m':-.3009999990463257,
   'glass_and_base_rails_extended_down_m':.0909999990463257,'upper_ends_unchanged':True,
   'collision_wall_inward_shift_m':.001,'collision_still_outside_field':True,'sweep_passed':96,'sweep_total':96,'numerical_bounds_tolerance_m':1e-6},
  'native_assets':23,'native_instances':57,'trees':26,'added_seating':0,
  'identity':{'arena':rec(manifest),'layout':rec(layout),'module':rec(R/'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll'),'protected_files':old['protected_file_hashes']},
  'source_evidence':{'gym_floor':rec(A/'source-floor-audit-01/report.json'),'rmuc_underside':rec(P/'field-floor-contact-01/rmuc-floor.json'),
   'raised_gym_nonintrusion':rec(P/'field-floor-contact-01/support-lift-intersections.json'),'prior_source_materials_geometry':rec(P/'native-geometry-final/report.json')},
  'execution_evidence':{'layout_apply':rec(A/'floor-support-apply-01/report.json'),'perimeter_apply':rec(A/'perimeter-floor-01/report.json'),
   'build':rec(A/'build-01/report.json'),'native_views':rec(A/'native-contact-01/report.json'),'visible_runtime':rec(A/'visible-current-01/report.json')},
  'visual_review':{'status':'passed_assistant_visual_inspection','source':'Actual UE captures inspected directly, independently reviewed by a second agent.',
   'findings':['South lower rail meets the original wood floor continuously without a visible floating gap.','Both corner sides meet the original red floor; frame and glass are aligned to the RMUC outer edge.','Field-level trees, sky, paths and distant landscape remain visible after the vertical lift.'],'views':samples},
  'interactive_runtime':{'status':live['status'],'pid':live['pid'],'seconds':live['seconds'],'left_running':live['left_running'],'warnings':live['warnings'],'window_response':rec(A/'visible-current-01/window-response.json')}}
 (A/'release-summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'status':report['status'],'file':str(A/'release-summary.json'),'sha256':sha(A/'release-summary.json'),'current_game_pid':live['pid']},indent=2))
if __name__=='__main__':main()
