"""UE Editor Python: import prepared visual and collision meshes for a source-derived map."""
import hashlib
import json
from pathlib import Path
import re
import sys
import unreal

sys.path.insert(0,str(Path(__file__).resolve().parent))
from import_vehicle_assets import import_asset, local_path


def material(folder,spec):
    name=spec['name']
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',name):raise ValueError('invalid material name')
    path=f'{folder}/Materials/{name}'
    m=unreal.load_asset(path) or unreal.AssetToolsHelpers.get_asset_tools().create_asset(name,f'{folder}/Materials',unreal.Material,unreal.MaterialFactoryNew())
    edit=unreal.MaterialEditingLibrary;edit.delete_all_material_expressions(m)
    color=edit.create_material_expression(m,unreal.MaterialExpressionConstant3Vector,-400,0)
    color.set_editor_property('constant',unreal.LinearColor(*spec['color_linear']))
    edit.connect_material_property(color,'',unreal.MaterialProperty.MP_BASE_COLOR)
    for row,(field,prop) in enumerate([('roughness',unreal.MaterialProperty.MP_ROUGHNESS),('metallic',unreal.MaterialProperty.MP_METALLIC)],1):
        node=edit.create_material_expression(m,unreal.MaterialExpressionConstant,-400,row*180)
        node.set_editor_property('r',float(spec[field]));edit.connect_material_property(node,'',prop)
    m.set_editor_property('two_sided',True)
    edit.recompile_material(m);unreal.EditorAssetLibrary.save_loaded_asset(m)
    return m


def main():
    match=re.search(r'-MapSource=(?:"([^"]+)"|(\S+))',unreal.SystemLibrary.get_command_line())
    if not match:raise ValueError('MapSource is required')
    path=Path(match.group(1) or match.group(2)).resolve();root=path.parent
    data=json.loads(path.read_text(encoding='utf-8'))
    if data['schema']!='rm_mesh_map_source_v1' or not re.fullmatch(r'[a-z][a-z0-9_]*',data['id']):raise ValueError('invalid source schema/id')
    folder='/Game/RMSim/Maps/'+data['id']
    mats={s['name']:material(folder,s) for s in data['materials']}
    imported={'visuals':{},'collisions':{}};bounds=[]
    for category,dirname in [('visuals','visual'),('collisions','collision')]:
        for spec in data[category]:
            filename=local_path(root,dirname+'/'+spec['file'])
            if hashlib.sha256(filename.read_bytes()).hexdigest()!=spec['sha256']:raise ValueError('source FBX hash mismatch')
            options=unreal.FbxImportUI()
            for key,value in {'import_mesh':True,'import_as_skeletal':False,'mesh_type_to_import':unreal.FBXImportType.FBXIT_STATIC_MESH,'import_materials':False,'import_textures':False}.items():options.set_editor_property(key,value)
            settings=options.get_editor_property('static_mesh_import_data')
            for key,value in {'combine_meshes':True,'convert_scene':True,'convert_scene_unit':True,'force_front_x_axis':True,'transform_vertex_to_absolute':True,'bake_pivot_in_vertex':False,'auto_generate_collision':False,'reorder_material_to_fbx_order':True}.items():settings.set_editor_property(key,value)
            settings.set_editor_property('normal_import_method',unreal.FBXNormalImportMethod.FBXNIM_IMPORT_NORMALS)
            objects=import_asset(filename,folder,dirname+'_'+spec['id'],options)
            meshes=[a for a in objects if isinstance(a,unreal.StaticMesh)]
            if len(meshes)!=1:raise RuntimeError('expected a single mesh')
            mesh=meshes[0]
            if category=='visuals':
                for i,slot in enumerate(mesh.get_editor_property('static_materials')):
                    name=str(slot.get_editor_property('material_slot_name'))
                    if name not in mats:raise ValueError('unknown CAD material slot '+name)
                    mesh.set_material(i,mats[name])
            else:
                body=mesh.get_editor_property('body_setup')
                body.set_editor_property('collision_trace_flag',unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
                body.set_editor_property('double_sided_geometry',True)
            unreal.EditorAssetLibrary.save_loaded_asset(mesh)
            imported[category][spec['id']]=mesh.get_path_name()
            box=mesh.get_bounding_box()
            bounds.append({'id':spec['id'],'kind':category,'ue_bounds_cm':{'lo':[box.min.x,box.min.y,box.min.z],'hi':[box.max.x,box.max.y,box.max.z]},'source_bounds_m':spec['bounds_m']})
            unreal.log('MAP_ASSET_IMPORTED '+category+'/'+spec['id'])
    if set(imported['visuals'])!=set(imported['collisions']):raise ValueError('visual/collision groups differ')
    all_lo=[min(s['bounds_m']['lo'][i] for s in data['visuals']) for i in range(3)]
    all_hi=[max(s['bounds_m']['hi'][i] for s in data['visuals']) for i in range(3)]
    source_hash=data.get('source_step_sha256') or data['source_sha256']
    result={'schema_version':1,'kind':'rm_mesh_map','id':data['id'],'display_name':data['display_name'],
        'units':'m','coordinate_system':'ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP','bounds_m':{'lo':all_lo,'hi':all_hi},
        'meshes':[{'id':key,'visual_asset':imported['visuals'][key],'collision_asset':imported['collisions'][key]} for key in imported['visuals']],
        'spawns':data.get('spawns',[{'id':'default','team':'blue','position_m':[-8,-2,0],'yaw_rad':0},{'id':'red','team':'red','position_m':[8,2,0],'yaw_rad':3.141592653589793}]),
        'regions':[],'source_sha256':source_hash,
        'limitations':data['limitations']}
    (root/'map.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    (root/'ue-import-report.json').write_text(json.dumps({'map_id':data['id'],'meshes':bounds,'assets_imported':len(bounds),'source_sha256':source_hash},indent=2),encoding='utf-8')
    unreal.log('MESH_MAP_IMPORT_COMPLETE '+str(root/'map.json'))


if __name__=='__main__':main()
