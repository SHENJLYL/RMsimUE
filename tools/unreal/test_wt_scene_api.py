"""Actual editor smoke for C++ map-authoring Python return contracts and saved levels.

Uses one duplicate engine cube and a tiny constant heightfield; it is explicitly a
test fixture, not evidence that the original Flanders map has been imported.
"""
import datetime
import json
from pathlib import Path
import struct
import sys
import unreal

sys.path.insert(0,str(Path(__file__).resolve().parent))
from import_wt_scene import unwrap, fbx_options

root=Path(__file__).resolve().parents[2]
output=root/'artifacts/maps/wt_existing'/('scene-api-check-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
output.mkdir(parents=True,exist_ok=False)
folder='/Game/RMSim/Maps/WT_ImportApiCheck'
cube_path=folder+'/Meshes/API_Cube'
cube=unreal.load_asset(cube_path) or unreal.EditorAssetLibrary.duplicate_asset('/Engine/BasicShapes/Cube',cube_path)
if not isinstance(cube,unreal.StaticMesh):raise RuntimeError('missing fixture cube')
body=cube.get_editor_property('body_setup')
body.set_editor_property('collision_trace_flag',unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
unreal.EditorAssetLibrary.save_loaded_asset(cube)
instances=output/'cube.rmi'
instances.write_bytes(b'RMI1'+struct.pack('<I10f',1,0,0,1,0,0,0,1,1,1,1))
(output/'instances.json').write_text(json.dumps(dict(parts=[dict(id='fixture_cube',visual_asset=cube.get_path_name(),
    collision_asset=cube.get_path_name(),instances_file='cube.rmi')])),encoding='utf-8')
width=128;height=128
(output/'height.u16').write_bytes(bytes(width*height*2))
material='/Game/RMSim/Maps/WT_MaterialCheck/Materials/WT_Terrain.WT_Terrain'
if not unreal.load_asset(material):raise RuntimeError('run test_wt_materials.py before this API check')
(output/'heightfield.json').write_text(json.dumps(dict(width=width,height=height,cell_m=.5,minimum_m=0,range_m=1,
    height_file='height.u16',material=material,origin_ros_m=[0,0,0])),encoding='utf-8')
fbx_options() # Checks every reflection name without needing a bulk mesh import.
world=unreal.EditorLoadingAndSavingUtils.new_blank_map(False)
raw_instances=unreal.MapImportLibrary.import_instances(world,str(output/'instances.json'))
count=unwrap(raw_instances,'ImportInstances')
if count!=1:raise RuntimeError('wrong imported fixture count')
raw_height=unreal.MapImportLibrary.import_heightfield(world,str(output/'heightfield.json'))
landscape=unwrap(raw_height,'ImportHeightfield')
if not isinstance(landscape,unreal.Landscape):raise RuntimeError('wrong heightfield return type')
saved=unreal.EditorLoadingAndSavingUtils.save_map(world,folder+'/World')
if not saved:raise RuntimeError('failed to save fixture world')
result=dict(status='passed',fixture=True,instances=count,instance_return_type=type(raw_instances).__name__,
    heightfield_return_type=type(raw_height).__name__,landscape=landscape.get_path_name(),map=folder+'/World',
    scope='C++ Python authoring API and map save only; original-map runtime validation remains separate')
(output/'result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
unreal.log('WT_SCENE_API_CHECK_COMPLETE '+str(output/'result.json'))
