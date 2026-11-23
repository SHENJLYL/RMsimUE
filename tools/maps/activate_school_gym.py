"""Activate the imported user-supplied school gym + CC0 campus as the RMUC visual shell.
Only changes RMUC gym manifest/layout after import has passed; preserves collision,
source field, robot and ROS navigation. Backups are created with full hashes.
"""
import argparse,json,hashlib,os,math
from pathlib import Path
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for chunk in iter(lambda:f.read(2**20),b''):h.update(chunk)
 return h.hexdigest()
def write_json(p,obj):
 p.parent.mkdir(parents=True,exist_ok=True)
 tmp=p.with_suffix(p.suffix+'.newtmp')
 if tmp.exists():raise FileExistsError('Stale temp output')
 tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 os.replace(tmp,p)
def ros(x):return [x[0],-x[1],x[2]]
def point(x,y,z):return [float(x),float(y),float(z)]
def make_instance(bmin,bmax,center=None):
 if center is None:center=[(a+b)*.5 for a,b in zip(bmin,bmax)]
 dims=[b-a for a,b in zip(bmin,bmax)]
 if not all(math.isfinite(x) and x>=0 for x in dims):raise ValueError('Nonpositive bounds')
 return {'center':ros(center),'size':dims,'rotation_deg':[0,0,0]}
def run():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('D:/simulator'))
 p.add_argument('--import-report',type=Path,required=True)
 p.add_argument('--out',type=Path,required=True);p.add_argument('--apply',action='store_true')
 args=p.parse_args()
 root=args.root.resolve()
 school=root/'assets/maps/rmuc2027/arena/sketchupbox-school-gym'
 bundle=school/'ue-import-03/prepared.json';d=json.loads(bundle.read_text(encoding='utf-8'))
 native=json.loads(args.import_report.read_text(encoding='utf-8'))
 if native.get('status')!='passed' or native.get('prepared_sha256')!=sha(bundle):
  raise RuntimeError('Native UE mesh import must pass on exact prepared source')
 if len(native['meshes'])!=len(d['meshes']) or len(d['meshes'])>32:raise RuntimeError('Incomplete import')
 expected={e['asset_name']:e for e in d['meshes']}
 mesh_paths={}
 for entry in native['meshes']:
  path=entry['asset']
  id_=path.rsplit('/',1)[-1].split('.')[0]
  if id_ not in expected or id_ in mesh_paths or not path.startswith('/Game/RMSim/Maps/rmuc2027/Arena/SchoolGym/Meshes/'):
   raise ValueError('Unverified mesh path: '+path)
  mesh_paths[id_]=path
 if set(mesh_paths)!=set(expected):raise RuntimeError('Missing assets')
 source=school/'source/Indoor Basketball Gym.skp'
 if sha(source)!=d['source_sha256']:raise RuntimeError('Original user SKP identity changed')
 arena_path=root/'assets/maps/rmuc2027/arena-shell.json'
 old_bytes=arena_path.read_bytes();arena=json.loads(old_bytes)
 if arena.get('map_id')!='rmuc2027' or arena['perimeter'].get('physics_contract')!='outside_source_bounds_v1':
  raise RuntimeError('Unknown field/perimeter baseline; do not activate')
 if abs(float(arena['perimeter'].get('outside_clearance_m',1))-.001)>1e-6:
  raise RuntimeError('Perimeter no longer touches the original source boundary')
 result={'schema':'rmsim_campus_gym_layout_v1','coordinate_system':'ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP','units':'m',
     'native_meshes':mesh_paths,'groups':[],'lights':[],
     'source':{'title':'Indoor Basketball Gym','source_url':'https://www.sketchupbox.com/202509210650315/','source_file_sha256':sha(source)},
     'prepared_sha256':sha(bundle),
     'source_geometry_policy':'Original source 55.47m x 38.12m x 14.02m retained. Basketball goal meshes removed; 3cm lower source court, field and vehicle untouched.',
     'campus_geometry_policy':'CC0 3D teaching buildings, windows, street furniture, tree and PBR grass. Visuals only; no world collision.'
 }
 for e in d['meshes']:
  id_=e['asset_name']
  if not id_.startswith('RMSchool_'):continue
  result['groups'].append({'id':id_,'mesh':id_,'material':'source','cast_shadow':True,'instances':[make_instance(e['bounds_min_m'],e['bounds_max_m'])]})
 def ext(id_,pts,shadows=True):
  e=expected[id_]
  result['groups'].append({'id':id_,'mesh':id_,'material':'source','cast_shadow':shadows,
                          'instances':[make_instance(e['bounds_min_m'],e['bounds_max_m'],c) for c in pts]})
 # Four ground cells form a 160 x 140 m campus context, entirely below CAD field level.
 ext('RMExteriorGround',[point(x,y,-.60) for x in (-40,40) for y in (-35,35)],False)
 # Repeated teaching wings positioned beyond gym exterior x=29/y=19m.
 ext('RMExteriorBuildings',[point(x,y,6.15) for x in (-19,19) for y in (-42,42)])
 ext('RMExteriorFurnishings',[point(x,y,2.35) for x in (-19,19) for y in (-29,29)],True)
 trees=[]
 for x in (-22,-12,0,12,22):
  for y in (-28,28):
   trees.append(point(x,y,3.92))
 for x in (-36,36):
  for y in (-16,-7,7,16):trees.append(point(x,y,3.92))
 for x in (-34,34):
  for y in (-33,33):trees.append(point(x,y,3.92))
 ext('RMExteriorTree',trees,True)
 # LED rows suspended below detailed 13m roof. Shadows and indirect lighting.
 for y in (-12,0,12):
  for x in (-19,-6.5,6.5,19):
   result['lights'].append({'center':ros([x,y,10.2]),'rotation_deg':[0,90,0],'width_m':2.5,'height_m':.55,'cast_shadows':True})
 assert len(result['native_meshes'])==19 and len(result['groups'])==19 and len(result['lights'])==12
 total=sum(len(x['instances']) for x in result['groups'])
 if total>12000:raise RuntimeError('Too many presentation instances')
 old_perimeter=json.dumps(arena['perimeter'],sort_keys=True)
 old_lights=json.dumps(arena['lighting'],sort_keys=True)
 arena['layout']='arena/sketchupbox-school-gym/layout.json'
 arena['source']='arena/sketchupbox-school-gym/SOURCE.json'
 arena['asset_license']='Original SketchupBox model project with attribution, CC0 exterior, official DJI / RoboMaster logos. Raw source must not be redistributed separately.'
 arena['notes']='User supplied original SketchUp 8 school gym, with native UE architecture, real 3D external campus, CC0 details, branded signs. Original RMUC field/vehicle/nav/physics retained.'
 arena['render_profile']='RMSimGymLumen'
 assert json.dumps(arena['perimeter'],sort_keys=True)==old_perimeter
 assert json.dumps(arena['lighting'],sort_keys=True)==old_lights
 out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
 (out/'arena-shell.before.json').write_bytes(old_bytes)
 write_json(out/'layout.candidate.json',result)
 write_json(out/'arena-shell.candidate.json',arena)
 summary={'status':'candidate_validated','source_sha256':sha(source),'native_report':str(args.import_report),
          'source_map_sha256':sha(root/'assets/maps/rmuc2027/map.json'),
          'vehicle_unchanged':True,'perimeter_unchanged':True,'lighting_unchanged':True,
          'native_mesh_count':len(result['native_meshes']),'campus_tree_instances':len(trees),
          'total_presentation_instances':total,'school_asset_blocks':len([g for g in result['groups'] if g['id'].startswith('RMSchool')]),
          'candidate_sha256':sha(out/'arena-shell.candidate.json')}
 if args.apply:
  if arena_path.read_bytes()!=old_bytes:raise RuntimeError('Concurrent map configuration changed')
  write_json(school/'layout.json',result)
  write_json(arena_path,arena)
  summary.update(status='applied_pending_runtime_qa',new_manifest_sha256=sha(arena_path))
 write_json(out/'report.json',summary)
 print(json.dumps(summary,indent=2),flush=True)
if __name__=='__main__':run()
