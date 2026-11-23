"""Activate the verified school fidelity assets and preserve competition/physics identities."""
from pathlib import Path
import argparse,json,hashlib,copy,datetime
r=Path('D:/simulator')
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
a=argparse.ArgumentParser();a.add_argument('--import-report',type=Path,required=True);a.add_argument('--geometry-report',type=Path,required=True);a.add_argument('--out',type=Path,required=True);args=a.parse_args()
p=r/'assets/maps/rmuc2027/arena-shell.json';lp=r/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/layout.json'
bundle=r/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/ue-import-05-fidelity/prepared.json'
native=json.loads(args.import_report.read_text(encoding='utf-8-sig'));proof=json.loads(args.geometry_report.read_text(encoding='utf-8-sig'));prepared=json.loads(bundle.read_text(encoding='utf-8-sig'))
if native['status']!='imported_pending_independent_geometry_and_visual_qa' or proof['status']!='passed':raise ValueError('Native import/geometry gate incomplete')
if native['prepared_sha256']!=sha(bundle) or proof['prepared_sha256']!=sha(bundle):raise ValueError('Bundle identity mismatch')
if len(proof['meshes'])!=23 or any(m['status']!='passed' for m in proof['meshes']):raise ValueError('Incomplete strict mesh gate')
before=p.read_bytes();lb=lp.read_bytes();arena=json.loads(before);old=json.loads(lb)
assert len(old['native_meshes'])==19 and len(old['groups'])==19
assert all(l['cast_shadows'] is False for l in old['lights'])
assert arena['perimeter']['physics_contract']=='outside_source_bounds_v1'
assert arena['perimeter']['outside_clearance_m']==.001
new=copy.deepcopy(old);new['groups']=[];new['native_meshes']={}
rows={x['asset'].rsplit('/',1)[-1].split('.')[0]:x for x in native['meshes']}
old_ext={g['id']:g for g in old['groups'] if g['id'].startswith('RMExterior')}
offset_ros=[-.0158752,7.1650915,0.]
for e in prepared['meshes']:
 name=e['asset_name'];n=rows[name];new['native_meshes'][name]=n['asset']
 lo=n['bounds_min_cm'];hi=n['bounds_max_cm'];dims=[(hi[k]-lo[k])*.01 for k in range(3)]
 if name.startswith('RMSchool_'):
  center=[(hi[k]+lo[k])*.005 for k in range(3)];center[1]=-center[1]
  new['groups'].append({'id':name,'mesh':name,'material':'source','cast_shadow':not name.startswith('RMSchool_4_'),'instances':[{'center':center,'size':dims,'rotation_deg':[0,0,0]}]})
 else:
  g=copy.deepcopy(old_ext[name])
  for i,inst in enumerate(g['instances']):
   if name=='RMExteriorTree':
    if i in (2,3):inst['center'][0]=-14.
    if i in (12,16):inst['center'][1]=-11.
   inst['center']=[inst['center'][k]+offset_ros[k] for k in range(3)]
   inst['size']=dims
   if name=='RMExteriorTree':inst['center'][2]=-0.6199999911+dims[2]*.5
   elif name=='RMExteriorBuildings':inst['center'][2]=-0.6199999911+dims[2]*.5
   elif name=='RMExteriorFurnishings':inst['center'][2]=-0.6199999911+dims[2]*.5
  new['groups'].append(g)
for light in new['lights']:
 light['center']=[light['center'][k]+offset_ros[k] for k in range(3)]
new['prepared_sha256']=sha(bundle)
new['source_geometry_policy']='Original school building, original benches, stairs and glazing retained. Two basketball goals omitted. Source sports-area center aligned to the unchanged RMUC court; no centroid triangle removal and no added spectator seating.'
new['campus_geometry_policy']='Full source tree geometry and 2K source textures. Exterior shifted with the school building; tree roots/buildings/furniture placed on actual grass surface. Visual-only groups.'
new['field_clearance_note']='Source hall is clear of the original RMUC field prism. Glass inner face remains exactly 1mm outside the unchanged CAD floor envelope; frame depth remains outward.'
new['native_layout_policy']='School group centers/sizes come from final native bounds so each imported native geometry retains identity scale/position.'
new['exterior_offset_ros_m']=offset_ros
new['source_verification']=str(args.geometry_report.relative_to(r))
new['sky_source']='arena/sketchupbox-school-gym/environment-fidelity/SOURCE.json'
arena['presentation_assets']=native['presentation_assets']
arena['lighting']['background_brightness']=512.
arena['lighting']['sun_rotation_ue_deg']=[-47.885557,124.258123,0.]
arena['lighting']['shadowed_rect_lights']=0
arena['notes']='Source-faithful SchoolGym, original benches only, clear original windows, full-detail 3D campus trees, 8K-source sky, source-envelope flush glass boundary.'
assert len(new['native_meshes'])==23 and len(new['groups'])==23
assert sum(len(g['instances']) for g in new['groups'])==53
assert len(new['lights'])==12 and all(not x['cast_shadows'] for x in new['lights'])
assert all('Seat' not in g['id'] and 'Tier' not in g['id'] for g in new['groups'])
o=args.out.resolve();o.mkdir(parents=True,exist_ok=False);(o/'arena.before.json').write_bytes(before);(o/'layout.before.json').write_bytes(lb)
identities={str(x.relative_to(r)):sha(x) for x in [r/'assets/maps/rmuc2027/map.json',r/'assets/vehicles/aim_sentry/ue_mesh_rig.json',r/'assets/vehicles/aim_sentry/drive-profile.json']}
(o/'layout.candidate.json').write_text(json.dumps(new,indent=2)+'\n',encoding='utf-8');(o/'arena.candidate.json').write_text(json.dumps(arena,indent=2)+'\n',encoding='utf-8')
if p.read_bytes()!=before or lp.read_bytes()!=lb:raise RuntimeError('Active layout changed concurrently')
lp.write_bytes((o/'layout.candidate.json').read_bytes());p.write_bytes((o/'arena.candidate.json').read_bytes())
assert all(sha(r/k)==v for k,v in identities.items())
summary={'status':'applied_pending_field_view_and_gpu_qa','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'manifest_sha256':sha(p),'layout_sha256':sha(lp),'prepared_sha256':sha(bundle),'geometry_report':str(args.geometry_report),'geometry_report_sha256':sha(args.geometry_report),'native_assets':23,'native_instances':53,'tree_instances':22,'new_seats':0,'source_hall_triangle_removal_except_goals':0,'physics_perimeter_unchanged':arena['perimeter']==json.loads(before)['perimeter'],'protected_file_hashes':identities,'source_skp_sha256':sha(r/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/source/Indoor Basketball Gym.skp')}
(o/'report.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8');print(json.dumps(summary,indent=2))
