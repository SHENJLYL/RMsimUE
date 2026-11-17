"""Blender: turn an audited float terrain crop into matching visual/collision FBXs."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import bpy
import numpy as np

p=argparse.ArgumentParser();p.add_argument('--package',required=True)
a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);root=Path(a.package).resolve()
source=json.loads((root/'height-source.json').read_text(encoding='utf-8'))
count=source['patch_samples'];cell=source['cell_size_m'];z=np.fromfile(root/'height.f32',dtype='<f4').reshape(count,count)
xy=(np.arange(count)-(count-1)/2)*cell
vertices=[(float(x),float(y),float(z[row,col])) for row,y in enumerate(xy) for col,x in enumerate(xy)]
faces=[]
for row in range(count-1):
    for col in range(count-1):
        i=row*count+col;faces.extend([(i,i+1,i+count+1),(i,i+count+1,i+count)])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.context.scene.unit_settings.system='METRIC';bpy.context.scene.unit_settings.scale_length=1
mesh=bpy.data.meshes.new('terrain');mesh.from_pydata(vertices,[],faces);mesh.update()
obj=bpy.data.objects.new('terrain',mesh);bpy.context.collection.objects.link(obj)
mat=bpy.data.materials.new('WT_CDK_Terrain_Preview');mat.diffuse_color=(.14,.22,.10,1)
mesh.materials.append(mat);obj.select_set(True);bpy.context.view_layer.objects.active=obj
bounds={'lo':list(np.min(vertices,axis=0)),'hi':list(np.max(vertices,axis=0))}
entries={}
for folder in ('visual','collision'):
    (root/folder).mkdir(exist_ok=True);target=root/folder/'terrain.fbx'
    bpy.ops.export_scene.fbx(filepath=str(target),use_selection=True,object_types={'MESH'},axis_forward='X',axis_up='Z',
        apply_unit_scale=True,apply_scale_options='FBX_SCALE_UNITS',use_mesh_modifiers=True,add_leaf_bones=False,bake_anim=False)
    entries[folder]={'id':'terrain','file':'terrain.fbx','bounds_m':bounds,'vertices':len(vertices),'triangles':len(faces),
        'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'material_slots':[mat.name]}
col=3*(count-1)//4;row=11*(count-1)//16
# Interior point of the first triangle in this cell, with the exact mesh surface height.
hillside=[float((col-(count-1)/2+.5)*cell),float((row-(count-1)/2+.25)*cell),
    float(z[row,col]+.5*(z[row,col+1]-z[row,col])+.25*(z[row+1,col+1]-z[row,col+1]))]
result={'schema':'rm_mesh_map_source_v1','id':'wt_cdk_crop','display_name':'War Thunder CDK terrain crop (512m)',
    'units':'m','coordinate_system':'ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP','source_sha256':source['source_sha256'],
    'visuals':[entries['visual']],'collisions':[entries['collision']],
    'materials':[{'name':mat.name,'color_linear':list(mat.diffuse_color),'roughness':.9,'metallic':0}],
    'spawns':[{'id':'default','team':'neutral','position_m':[0,0,0],'yaw_rad':0},
        {'id':'hillside','team':'neutral','position_m':hillside,'yaw_rad':0}],
    'limitations':source['limitations']+['plain preview material; not the original War Thunder ground appearance']}
(root/'mesh-source.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print('WT_CDK_MESH_PREPARED',len(faces),'triangles')
