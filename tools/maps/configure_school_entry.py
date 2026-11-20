"""Activate verified CC0 entrance walls and an independent outdoor background.

The original 23 school/exterior groups and the competition perimeter remain
byte-equivalent JSON values. The original map/vehicle/navigation files are read
only. Run after native import and strict geometry verification.
"""
from pathlib import Path
import argparse, copy, hashlib, json, shutil, datetime

ROOT=Path(__file__).resolve().parents[2]
LAYOUT=ROOT/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/layout.json'
MANIFEST=ROOT/'assets/maps/rmuc2027/arena-shell.json'
PREPARED=ROOT/'assets/maps/rmuc2027/arena/school-entry-brickwall/prepared/prepared.json'
BASE='/Game/RMSim/Maps/rmuc2027/Arena/SchoolEntry'
ASSET='RMExteriorEntryBrickWall'
OLD_LAYOUT_SHA='64bfb32766056f33a4b706c2656472f3d2d7f954187128cd2b9cc37a91710300'
OLD_MANIFEST_SHA='0a9f3441fa826c5bb1274c7c3b08d1a9377f8b9fffe2060811331639e672ed9e'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def write(p,value):Path(p).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8',newline='\n')
def main():
    a=argparse.ArgumentParser()
    a.add_argument('--native-import-report',type=Path,required=True)
    a.add_argument('--out',type=Path,required=True)
    a.add_argument('--apply',action='store_true')
    x=a.parse_args()
    if sha(LAYOUT)!=OLD_LAYOUT_SHA or sha(MANIFEST)!=OLD_MANIFEST_SHA:
        raise ValueError('Active source differs from the accepted wave-wall baseline')
    native=read(x.native_import_report);prepared=read(PREPARED)
    if native['status']!='native_import_and_geometry_passed_pending_scene_visual_qa':
        raise ValueError('Native assets have not passed independent geometry verification')
    if native['prepared_sha256']!=sha(PREPARED) or native['native_base']!=BASE:
        raise ValueError('Native import does not match the prepared entry source')
    if len(native['meshes'])!=1 or native['meshes'][0]['independent_geometry_status']!='passed':
        raise ValueError('Expected one fully verified wall mesh')
    if native['distant_ground_material']['asset']!=BASE+'/Materials/M_ExteriorDistantGround.M_ExteriorDistantGround':
        raise ValueError('Verified distant-ground material is missing')
    old_layout=read(LAYOUT);old_manifest=read(MANIFEST)
    scene=copy.deepcopy(old_layout);manifest=copy.deepcopy(old_manifest)
    if len(scene['native_meshes'])!=23 or sum(len(g['instances']) for g in scene['groups'])!=57:
        raise ValueError('Unexpected original school/exterior instance count')
    if ASSET in scene['native_meshes']:raise ValueError('Entry asset already active')
    mesh=prepared['meshes'][0]
    if mesh['asset_name']!=ASSET:raise ValueError('Wrong prepared entry mesh')
    scale=.85;size=[(mesh['bounds_max_m'][k]-mesh['bounds_min_m'][k])*scale for k in range(3)]
    if abs(size[0]-2.55)>1.e-6 or abs(size[1]-.272)>1.e-6:
        raise ValueError('Unexpected source brick wall dimensions')
    surface=-.48440000057220454;bury=.015
    lower_end=-2.4712915039;upper_start=16.8;segments=6
    opening=[lower_end,upper_start]
    length=segments*size[0]
    instances=[];wings=[]
    for side,inner,sign in [('west',-32.0158752,-1),('east',31.9841248,1)]:
        cx=inner+sign*size[1]/2
        for wing,start in [('lower',lower_end-length),('upper',upper_start)]:
            wings.append({'side':side,'wing':wing,'inner_face_ros_x_m':inner,
                          'y_range_m':[start,start+length],'segments':segments})
            for i in range(segments):
                instances.append({'center':[cx,start+(i+.5)*size[0],surface-bury+size[2]/2],
                                  'size':size,'rotation_deg':[0,0,90]})
    scene['native_meshes'][ASSET]=BASE+'/Meshes/'+ASSET+'.'+ASSET
    scene['groups'].append({'id':ASSET,'mesh':ASSET,'material':'source','cast_shadow':True,'instances':instances})
    scene.setdefault('metadata',{})['entry_masonry']={
        'source_title':'Brick Wall','author':'Grab3D','license':'CC0-1.0',
        'source_url':'https://grab3d.com/models/city/brick-wall/',
        'source_sha256':prepared['source_file_sha256'],'prepared_sha256':sha(PREPARED),
        'native_import_report':x.native_import_report.resolve().relative_to(ROOT).as_posix(),
        'uniform_scale':scale,'instances':len(instances),'wings':wings,
        'grass_surface_z_m':surface,'base_burial_m':bury,'visible_height_m':size[2]-bury,
        'entrance_opening_ros_y_m':opening,'entrance_clear_width_m':upper_start-lower_end,
        'outer_stair_to_wall_inner_face_min_m':4.2631994,
        'upper_path_y_range_m':[13.7650915,16.5650915],
        'upper_path_to_wall_endpoint_m':upper_start-16.5650915,
        'geometry_policy':'Complete source brick module, original PBR and UVs; all original school/exterior groups retained',
        'collision_policy':'Cosmetic outdoor group, same no-collision/no-navigation policy as the exterior'}
    manifest['outdoor_background']={
        'sky_diameter_m':16000,'ground_half_extent_m':2000,
        'ground_surface_z_m':surface,
        'ground_material':BASE+'/Materials/M_ExteriorDistantGround.M_ExteriorDistantGround',
        'ground_inner_bounds_m':{'lo':[-80.0158752,-62.8349085,surface],
                                 'hi':[79.9841248,77.1650915,surface]}}
    # Same-camera outdoor comparisons retain readable brickwork in tree shade.
    # The source textures, sun, exposure and twelve original rect lights stay fixed.
    manifest['lighting']['sky_intensity']=512.0
    manifest['notes']=old_manifest['notes']+' CC0 brick entrance walls preserve the existing paths; an independent distant ground ring encloses the original campus ground under the enlarged sky background.'
    assert scene['groups'][:-1]==old_layout['groups']
    assert all(scene['native_meshes'][k]==v for k,v in old_layout['native_meshes'].items())
    assert manifest['perimeter']==old_manifest['perimeter']
    assert manifest['bounds_m']==old_manifest['bounds_m']
    assert scene['lights']==old_layout['lights']
    x.out.mkdir(parents=True,exist_ok=False)
    write(x.out/'layout-proposed.json',scene);write(x.out/'arena-proposed.json',manifest)
    protected=[ROOT/'assets/maps/rmuc2027/map.json',ROOT/'assets/maps/rmuc2027/mesh-source.json',
               ROOT/'assets/vehicles/aim_sentry/ue_mesh_rig.json',
               ROOT/'assets/maps/rmuc2027/navigation-console-platform/scene.json']
    report={'status':'proposed','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'before':{'manifest_sha256':sha(MANIFEST),'layout_sha256':sha(LAYOUT)},
            'protected_files':{p.relative_to(ROOT).as_posix():sha(p) for p in protected},
            'native_assets':len(scene['native_meshes']),'native_instances':sum(len(g['instances']) for g in scene['groups']),
            'entry_masonry':scene['metadata']['entry_masonry'],'outdoor_background':manifest['outdoor_background'],
            'original_groups_unchanged':True,'original_perimeter_unchanged':True,'source_rect_light_rig_unchanged':True,
            'sky_light_intensity_change':{'before':old_manifest['lighting']['sky_intensity'],'after':512.0}}
    if x.apply:
        shutil.copy2(LAYOUT,x.out/'layout-before.json');shutil.copy2(MANIFEST,x.out/'arena-before.json')
        original_bytes={LAYOUT:LAYOUT.read_bytes(),MANIFEST:MANIFEST.read_bytes()}
        try:
            write(LAYOUT,scene);write(MANIFEST,manifest)
            if read(LAYOUT)!=scene or read(MANIFEST)!=manifest:raise RuntimeError('Activation readback differs')
            if any(sha(ROOT/path)!=value for path,value in report['protected_files'].items()):
                raise RuntimeError('Protected source identity changed')
        except Exception:
            for path,data in original_bytes.items():path.write_bytes(data)
            raise
        report['status']='activated_pending_native_visual_qa'
        report['after']={'manifest_sha256':sha(MANIFEST),'layout_sha256':sha(LAYOUT)}
    write(x.out/'report.json',report)
    print(json.dumps({'status':report['status'],'native_assets':report['native_assets'],
                      'native_instances':report['native_instances'],'report':str(x.out/'report.json')}),flush=True)
if __name__=='__main__':main()
