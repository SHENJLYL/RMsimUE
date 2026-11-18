"""UE Editor commandlet: import the prepared original Flanders scene.

UnrealEditor-Cmd RMSim.uproject -run=pythonscript -script=<this file>
    -SceneSource=<assets/maps/wt_vlaanderen/scene-source.json>

Imports resumable source-hashed FBXs, creates the saved editor world, and records
actual asset bounds/counts. It does not certify gameplay or native Dagor visuals.
"""
import datetime
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

import unreal

sys.path.insert(0,str(Path(__file__).resolve().parent))
from import_vehicle_assets import import_asset, local_path
from wt_materials import create_materials, material_id
from wt_material_slots import material_bindings
from wt_mesh_geometry import triangle_accounting, verify_native_material_triangles
from wt_vertex_opacity import validate_scene_vertex_opacity, validate_vertex_opacity
from wt_runtime_virtual_texture import bind_rvt_world
from wt_landclass import validate_landclass_surface
from wt_overview import import_overview

IMPORT_VERSION = 3


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def save_json(path,data):
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    temp.replace(path)


def asset_file(asset_path):
    if not asset_path.startswith('/Game/'):
        raise ValueError('asset is outside project content')
    name=asset_path.split('.')[0][len('/Game/'):]
    return Path(unreal.Paths.project_content_dir()).resolve()/(name+'.uasset')


def safe_asset_path(path,folder):
    if not path.startswith(folder+'/') or '..' in path:
        raise ValueError('scene asset escapes destination: '+path)
    base=path.split('/')[-1].split('.')
    if len(base)!=2 or base[0]!=base[1] or not re.fullmatch('[A-Za-z][A-Za-z0-9_]*',base[0]):
        raise ValueError('invalid UE object reference: '+path)
    return path.rsplit('/',1)[0],base[0]


def validate_source(package,source,folder):
    if source.get('schema')!='rm_scene_map_source_v1' or source.get('units')!='m' or source.get('coordinate_system')!='ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP':
        raise ValueError('unsupported source schema/units/coordinates')
    if not re.fullmatch('[a-z][a-z0-9_]*',source.get('id','')) or not re.fullmatch('[0-9a-f]{64}',source.get('source_sha256','')):
        raise ValueError('invalid scene identity/provenance')
    material_ids={}
    for spec in source['materials']:
        identity=material_id(spec)
        if spec['id']!=identity or identity in material_ids:
            raise ValueError('invalid/duplicate material ID')
        material_ids[identity]=spec
    visual={};collision={}
    for label,entries,target in [('Meshes',source['meshes'],visual),('Collision',source['collision_meshes'],collision)]:
        for spec in entries:
            name=spec['id']
            if not re.fullmatch('[A-Za-z][A-Za-z0-9_]*',name) or name in target:
                raise ValueError('unsafe/duplicate mesh ID')
            path=local_path(package,spec['file'])
            if digest(path)!=spec['sha256']:
                raise ValueError('source FBX hash mismatch: '+str(path))
            triangle_accounting(package,spec,source['source_sha256'])
            if label=='Meshes' and any(s not in material_ids for s in spec['material_slots']):
                raise ValueError('visual references unknown material')
            target[folder+'/'+label+'/'+name+'.'+name]=spec
    validate_scene_vertex_opacity(package,source)
    validate_landclass_surface(package,source)
    instance_path=local_path(package,source['instances']);instances=read(instance_path)
    seen=set();count=0;collision_paths=set();instance_hashes={}
    for part in instances['parts']:
        if part['id'] in seen or part['visual_asset'] not in visual:
            raise ValueError('invalid/duplicate visual instance part')
        seen.add(part['id'])
        safe_asset_path(part['visual_asset'],folder)
        col=part['collision_asset']
        if col:
            safe_asset_path(col,folder)
            if col not in visual and col not in collision:
                raise ValueError('unresolved collision asset '+col)
            collision_paths.add(col)
        path=local_path(package,part['instances_file']);raw=path.read_bytes()
        if len(raw)<8 or raw[:4]!=b'RMI1':raise ValueError('invalid RMI instance file')
        number=struct.unpack_from('<I',raw,4)[0]
        if len(raw)!=8+number*40 or not 0<number<=1000000:raise ValueError('invalid RMI size/count')
        if number!=visual[part['visual_asset']]['instance_count']:raise ValueError('RMI/manifest count mismatch')
        count+=number;instance_hashes[part['instances_file']]=hashlib.sha256(raw).hexdigest()
    if len(seen)!=len(visual):raise ValueError('unplaced visual mesh in source manifest')
    height_path=local_path(package,source['terrain']);height=read(height_path)
    height_data=local_path(package,height['height_file'])
    if digest(height_data)!=height['source_samples_sha256'] or height_data.stat().st_size!=height['width']*height['height']*2:
        raise ValueError('heightfield source bytes differ from metadata')
    safe_asset_path(height['material'],folder)
    return visual,collision,collision_paths,count,instance_hashes,instance_path,height_path


def fbx_options():
    options=unreal.FbxImportUI()
    for key,value in dict(import_mesh=True,import_as_skeletal=False,
        mesh_type_to_import=unreal.FBXImportType.FBXIT_STATIC_MESH,import_materials=False,import_textures=False).items():
        options.set_editor_property(key,value)
    settings=options.get_editor_property('static_mesh_import_data')
    for key,value in dict(combine_meshes=True,convert_scene=True,convert_scene_unit=True,
        force_front_x_axis=True,transform_vertex_to_absolute=True,bake_pivot_in_vertex=False,
        auto_generate_collision=False,reorder_material_to_fbx_order=True,generate_lightmap_u_vs=False,
        remove_degenerates=False).items():
        settings.set_editor_property(key,value)
    settings.set_editor_property('normal_import_method',unreal.FBXNormalImportMethod.FBXNIM_IMPORT_NORMALS)
    return options


def bounds_record(mesh,spec):
    bounds=mesh.get_bounding_box()
    actual=dict(lo=[bounds.min.x,bounds.min.y,bounds.min.z],hi=[bounds.max.x,bounds.max.y,bounds.max.z])
    expected={edge:[spec['bounds_m'][('hi' if edge=='lo' else 'lo') if axis==1 else edge][axis]*(-100 if axis==1 else 100)
                    for axis in range(3)] for edge in ('lo','hi')}
    error=max(abs(actual[edge][axis]-expected[edge][axis]) for edge in ('lo','hi') for axis in range(3))
    # Whole-world static meshes reach 16km from origin (1.6 million cm). A float ULP is larger there;
    # retain both absolute error and scale, rather than claiming real-world measurement.
    magnitude=max(abs(v) for values in expected.values() for v in values)
    tolerance=max(.01,magnitude*2e-7)
    if error>tolerance:raise ValueError(f'{spec["id"]}: source/import bounds differ by {error}cm (limit {tolerance})')
    return dict(ue_bounds_cm=actual,source_bounds_m=spec['bounds_m'],max_error_cm=error,tolerance_cm=tolerance)


def extend_billboard_bounds(mesh,spec):
    """Cover every no-wind view rotation using source centre +/- offset length."""
    source_bounds=spec.get('billboard_bounds_m')
    if source_bounds is None:return None
    expected={edge:[source_bounds[('hi' if edge=='lo' else 'lo') if axis==1 else edge][axis]*(-100 if axis==1 else 100)
                    for axis in range(3)] for edge in ('lo','hi')}
    base=mesh.get_bounding_box()
    low=[base.min.x,base.min.y,base.min.z];high=[base.max.x,base.max.y,base.max.z]
    negative=[max(0,low[i]-expected['lo'][i]) for i in range(3)]
    positive=[max(0,expected['hi'][i]-high[i]) for i in range(3)]
    mesh.set_editor_property('negative_bounds_extension',unreal.Vector(*negative))
    mesh.set_editor_property('positive_bounds_extension',unreal.Vector(*positive))
    final=mesh.get_bounding_box()
    actual=dict(lo=[final.min.x,final.min.y,final.min.z],hi=[final.max.x,final.max.y,final.max.z])
    if any(actual['lo'][i]>expected['lo'][i]+.01 or actual['hi'][i]<expected['hi'][i]-.01 for i in range(3)):
        raise ValueError('billboard culling bounds do not contain the source offset sphere')
    return dict(source_bounds_m=source_bounds,ue_bounds_cm=actual,
                negative_extension_cm=negative,positive_extension_cm=positive)


def import_mesh(package,asset_path,spec,collision,mats,cache,cache_file,folder,scene_source_sha256=None):
    destination,name=safe_asset_path(asset_path,folder)
    geometry_accounting=triangle_accounting(package,spec,scene_source_sha256)
    vertex_opacity=validate_vertex_opacity(package,spec,scene_source_sha256)
    signature=dict(fbx_sha256=spec['sha256'],import_version=IMPORT_VERSION,
                   material_slots=spec['material_slots'],triangle_collision=collision)
    if geometry_accounting['geometry_audit_sha256'] is not None:
        signature['geometry_audit_sha256']=geometry_accounting['geometry_audit_sha256']
    if vertex_opacity is not None:
        signature['source_vertex_opacity']=spec['source_vertex_opacity']
    if 'billboard_bounds_m' in spec:
        signature['billboard_bounds_m']=spec['billboard_bounds_m']
        signature['leaf_adapter']=spec['leaf_adapter']
    old=cache['assets'].get(asset_path,{})
    mesh=unreal.load_asset(asset_path) if unreal.EditorAssetLibrary.does_asset_exist(asset_path) else None
    saved_file=asset_file(asset_path)
    reuse=isinstance(mesh,unreal.StaticMesh) and old.get('signature')==signature and saved_file.is_file() and old.get('ue_file_sha256')==digest(saved_file)
    if not reuse:
        objects=import_asset(local_path(package,spec['file']),destination,name,fbx_options())
        meshes=[m for m in objects if isinstance(m,unreal.StaticMesh)]
        if len(meshes)!=1 or meshes[0].get_path_name()!=asset_path:raise ValueError('FBX import did not yield requested single static mesh')
        mesh=meshes[0]
    if not isinstance(mesh,unreal.StaticMesh):raise ValueError('mesh unavailable '+asset_path)
    slots=mesh.get_editor_property('static_materials')
    actual_slots=[str(slot.get_editor_property('material_slot_name')) for slot in slots]
    bindings=material_bindings(spec['material_slots'],actual_slots)
    for i,identity in enumerate(bindings):
        if identity in mats:
            target_material=mats[identity]
        elif identity=='WT_SourceCollision':
            target_material=None
        else:
            raise ValueError('unresolved imported material slot '+identity)
        current_material=mesh.get_material(i)
        current_path=current_material.get_path_name() if current_material else None
        target_path=target_material.get_path_name() if target_material else None
        if current_path!=target_path:
            mesh.set_material(i,target_material)
    if spec.get('leaf_adapter'):
        # UE Python omits a bool success return when packing out parameters:
        # true + FString& yields the error string, false yields None.
        leaf_error=unreal.MapImportLibrary.prepare_source_leaf_mesh(mesh)
        if not isinstance(leaf_error,str) or leaf_error:
            raise RuntimeError('source leaf mesh precision preparation failed: '+str(leaf_error))
    if vertex_opacity is not None:
        opacity_error=unreal.MapImportLibrary.prepare_source_precision_mesh(mesh,2)
        if not isinstance(opacity_error,str) or opacity_error:
            raise RuntimeError('source vertex opacity precision preparation failed: '+str(opacity_error))
    native_geometry=json.loads(unreal.MapImportLibrary.describe_map_mesh(mesh))
    if vertex_opacity is not None and (native_geometry['uv_channels']!=2 or not native_geometry['full_precision_uvs']):
        raise ValueError('native vertex opacity did not retain two full-precision UVs')
    imported_triangles=native_geometry['triangles']
    if spec.get('leaf_adapter') and (native_geometry['uv_channels']!=5 or not native_geometry['full_precision_uvs']):
        raise ValueError('native leaf offset channels did not retain five full-precision UVs')
    expected_triangles=geometry_accounting['expected_native_triangles']
    if imported_triangles!=expected_triangles:
        raise ValueError(f'{name}: imported {imported_triangles} triangles, expected {expected_triangles}; '
                         f'original={spec["triangles"]}, proven_coincident={geometry_accounting["strictly_coincident_source_triangles"]}')
    verified_material_triangles=verify_native_material_triangles(geometry_accounting,native_geometry)
    if verified_material_triangles is not None:
        geometry_accounting['verified_native_material_triangles']=verified_material_triangles
    body=mesh.get_editor_property('body_setup')
    if collision:
        if body is None:raise ValueError('no mesh BodySetup for source collision')
        body.set_editor_property('collision_trace_flag',unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
        body.set_editor_property('double_sided_geometry',True)
    if 'billboard_bounds_m' in spec:
        mesh.set_editor_property('negative_bounds_extension',unreal.Vector(0,0,0))
        mesh.set_editor_property('positive_bounds_extension',unreal.Vector(0,0,0))
    record=bounds_record(mesh,spec)
    billboard_bounds=extend_billboard_bounds(mesh,spec)
    if not unreal.EditorAssetLibrary.save_loaded_asset(mesh,only_if_is_dirty=False):raise RuntimeError('failed to save mesh '+name)
    if not saved_file.is_file():raise RuntimeError('saved asset file absent '+str(saved_file))
    cache['assets'][asset_path]=dict(signature=signature,ue_file_sha256=digest(saved_file),bounds=record)
    save_json(cache_file,cache)
    return dict(id=name,asset=asset_path,reused=reuse,source_sha256=spec['sha256'],
                triangle_collision=collision,source_triangles=spec['triangles'],imported_triangles=imported_triangles,
                geometry_accounting=geometry_accounting,source_vertex_opacity=vertex_opacity,
                source_material_slots=spec['material_slots'],imported_material_slots=actual_slots,
                billboard_bounds=billboard_bounds,native_geometry=native_geometry,**record)


def unwrap(result,label):
    # Unreal exposes non-const FString& as an output alongside the function return.
    if isinstance(result,tuple):
        if len(result)!=2:raise RuntimeError(label+' returned unexpected output tuple')
        value,error=result
        if error:raise RuntimeError(label+': '+str(error))
        return value
    raise RuntimeError(label+' output contract changed; expected(return_value,error), got '+type(result).__name__)


def import_scene(source_file):
    source_file=Path(source_file).resolve(strict=True);package=source_file.parent;source=read(source_file)
    folder='/Game/RMSim/Maps/'+source['id'];world_path=folder+'/World'
    visual,collision,collision_paths,instance_count,instance_hashes,instance_path,height_path=validate_source(package,source,folder)
    cache_file=package/'ue-mesh-import-cache.json'
    cache=read(cache_file) if cache_file.exists() else dict(schema='wt_scene_mesh_cache_v1',assets={})
    if cache.get('schema')!='wt_scene_mesh_cache_v1':raise ValueError('unknown mesh cache format')
    started=datetime.datetime.now(datetime.timezone.utc)
    evidence=package.parents[2]/'artifacts/maps/wt_existing'/('scene-import-'+started.strftime('%Y%m%dT%H%M%SZ'))
    evidence.mkdir(parents=True,exist_ok=False)
    report=dict(status='running',started_utc=started.isoformat(),source_sha256=source['source_sha256'],
        manifest_sha256=digest(source_file),source_manifest=str(source_file),engine=str(unreal.SystemLibrary.get_engine_version()),
        import_version=IMPORT_VERSION,
        importer_scripts_sha256={name:digest(Path(__file__).resolve().with_name(name)) for name in
                                 ('import_wt_scene.py','wt_materials.py','wt_material_slots.py','wt_mesh_geometry.py','wt_vertex_opacity.py','wt_runtime_virtual_texture.py','wt_tree_alpha.py','wt_landclass.py','wt_overview.py','wt_water.py')},
        map_asset=world_path,mesh_results=[],runtime_verified=False,source_limits=source['limitations'])
    def record():save_json(evidence/'report.json',report)
    record()
    try:
        report['overview']=import_overview(package,folder)
        mats,terrain=create_materials(package,folder)
        if terrain.get_path_name()!=read(height_path)['material']:raise ValueError('terrain material path differs from heightfield declaration')
        for i,(asset,spec) in enumerate([*visual.items(),*collision.items()]):
            report['mesh_results'].append(import_mesh(package,asset,spec,asset in collision_paths,mats,cache,cache_file,folder,
                                                      scene_source_sha256=source['source_sha256']))
            record()
            if (i+1)%25==0:
                unreal.log(f'WT_SCENE_MESHES {i+1}/{len(visual)+len(collision)}')
                # The report holds only scalar evidence. Release unused editor
                # import objects before the next large baked source mesh.
                unreal.SystemLibrary.collect_garbage()
        world=unreal.EditorLoadingAndSavingUtils.new_blank_map(False)
        if world is None:raise RuntimeError('cannot create empty editor world')
        imported_count=unwrap(unreal.MapImportLibrary.import_instances(world,str(instance_path)),'ImportInstances')
        if imported_count!=instance_count:raise ValueError(f'instance count mismatch {imported_count} vs {instance_count}')
        landscape=unwrap(unreal.MapImportLibrary.import_heightfield(world,str(height_path)),'ImportHeightfield')
        if not isinstance(landscape,unreal.Landscape):raise RuntimeError('heightfield did not produce Landscape')
        report['ground_decal_rvt']=bind_rvt_world(world,source)
        record()
        settings=unreal.GameplayStatics.get_all_actors_of_class(world,unreal.WorldSettings)
        if len(settings)!=1:raise RuntimeError('scene must contain exactly one WorldSettings actor')
        tags=[str(tag) for tag in settings[0].get_editor_property('tags')
              if not str(tag).startswith(('RMSimMapId=','RMSimSourceSha256='))]
        tags.extend(['RMSimMapId='+source['id'],'RMSimSourceSha256='+source['source_sha256']])
        settings[0].set_editor_property('tags',tags)
        if not unreal.EditorLoadingAndSavingUtils.save_map(world,world_path):raise RuntimeError('cannot save imported map')
        material_report=read(package/'material-import-report.json')
        report.update(status='imported_pending_runtime_validation',finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            visual_assets=len(visual),collision_assets=len(collision),collision_referenced_assets=len(collision_paths),
            instances_imported=imported_count,instance_file_sha256=instance_hashes,
            source_stored_instances=source['source_coverage']['source_instances'],
            world_identity_tags=tags,
            map_file_sha256=digest(Path(unreal.Paths.project_content_dir()).resolve()/('RMSim/Maps/'+source['id']+'/World.umap')),
            landscape_actor=landscape.get_path_name(),heightfield=read(height_path),
            material_count=len(mats),native_shader_parity=False,
            unresolved_shader_bindings=material_report['unresolved_shader_bindings'],
            material_limits=material_report['limitations'])
        save_json(package/'ue-scene-import-report.json',report)
        unreal.log('WT_SCENE_IMPORT_COMPLETE '+str(evidence/'report.json'))
    except Exception as error:
        report.update(status='failed',error=str(error))
        raise
    finally:
        record()
    return report


if __name__=='__main__':
    match=re.search(r'-SceneSource=(?:"([^"]+)"|(\S+))',unreal.SystemLibrary.get_command_line())
    if not match:raise ValueError('pass -SceneSource=<scene-source.json>')
    import_scene(match.group(1) or match.group(2))
