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

IMPORT_VERSION = 1


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
            if label=='Meshes' and any(s not in material_ids for s in spec['material_slots']):
                raise ValueError('visual references unknown material')
            target[folder+'/'+label+'/'+name+'.'+name]=spec
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
        auto_generate_collision=False,reorder_material_to_fbx_order=True,generate_lightmap_u_vs=False).items():
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


def import_mesh(package,asset_path,spec,collision,mats,cache,cache_file,folder):
    destination,name=safe_asset_path(asset_path,folder)
    signature=dict(fbx_sha256=spec['sha256'],import_version=IMPORT_VERSION,
                   material_slots=spec['material_slots'],triangle_collision=collision)
    old=cache['assets'].get(asset_path,{})
    mesh=unreal.load_asset(asset_path)
    saved_file=asset_file(asset_path)
    reuse=isinstance(mesh,unreal.StaticMesh) and old.get('signature')==signature and saved_file.is_file() and old.get('ue_file_sha256')==digest(saved_file)
    if not reuse:
        objects=import_asset(local_path(package,spec['file']),destination,name,fbx_options())
        meshes=[m for m in objects if isinstance(m,unreal.StaticMesh)]
        if len(meshes)!=1 or meshes[0].get_path_name()!=asset_path:raise ValueError('FBX import did not yield requested single static mesh')
        mesh=meshes[0]
    if not isinstance(mesh,unreal.StaticMesh):raise ValueError('mesh unavailable '+asset_path)
    slots=mesh.get_editor_property('static_materials')
    if len(slots)!=len(spec['material_slots']):raise ValueError('FBX material slot count differs '+name)
    for i,slot in enumerate(slots):
        actual=str(slot.get_editor_property('material_slot_name'));expected=spec['material_slots'][i]
        if actual!=expected:raise ValueError(f'{name}: slot{i} got{actual}, expected{expected}')
        if expected in mats:mesh.set_material(i,mats[expected])
        elif expected=='WT_SourceCollision':mesh.set_material(i,None)
        else:raise ValueError('unresolved imported material slot '+expected)
    body=mesh.get_editor_property('body_setup')
    if collision:
        if body is None:raise ValueError('no mesh BodySetup for source collision')
        body.set_editor_property('collision_trace_flag',unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
        body.set_editor_property('double_sided_geometry',True)
    record=bounds_record(mesh,spec)
    if not unreal.EditorAssetLibrary.save_loaded_asset(mesh,only_if_is_dirty=False):raise RuntimeError('failed to save mesh '+name)
    if not saved_file.is_file():raise RuntimeError('saved asset file absent '+str(saved_file))
    cache['assets'][asset_path]=dict(signature=signature,ue_file_sha256=digest(saved_file),bounds=record)
    save_json(cache_file,cache)
    return dict(id=name,asset=asset_path,reused=reuse,source_sha256=spec['sha256'],
                triangle_collision=collision,source_triangles=spec['triangles'],**record)


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
        map_asset=world_path,mesh_results=[],runtime_verified=False,source_limits=source['limitations'])
    def record():save_json(evidence/'report.json',report)
    record()
    try:
        mats,terrain=create_materials(package,folder)
        if terrain.get_path_name()!=read(height_path)['material']:raise ValueError('terrain material path differs from heightfield declaration')
        for i,(asset,spec) in enumerate([*visual.items(),*collision.items()]):
            report['mesh_results'].append(import_mesh(package,asset,spec,asset in collision_paths,mats,cache,cache_file,folder))
            record()
            if (i+1)%25==0:unreal.log(f'WT_SCENE_MESHES {i+1}/{len(visual)+len(collision)}')
        world=unreal.EditorLoadingAndSavingUtils.new_blank_map(False)
        if world is None:raise RuntimeError('cannot create empty editor world')
        imported_count=unwrap(unreal.MapImportLibrary.import_instances(world,str(instance_path)),'ImportInstances')
        if imported_count!=instance_count:raise ValueError(f'instance count mismatch {imported_count} vs {instance_count}')
        landscape=unwrap(unreal.MapImportLibrary.import_heightfield(world,str(height_path)),'ImportHeightfield')
        if not isinstance(landscape,unreal.Landscape):raise RuntimeError('heightfield did not produce Landscape')
        if not unreal.EditorLoadingAndSavingUtils.save_map(world,world_path):raise RuntimeError('cannot save imported map')
        material_report=read(package/'material-import-report.json')
        report.update(status='imported_pending_runtime_validation',finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            visual_assets=len(visual),collision_assets=len(collision),collision_referenced_assets=len(collision_paths),
            instances_imported=imported_count,instance_file_sha256=instance_hashes,
            source_stored_instances=source['source_coverage']['source_instances'],
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
