"""Incrementally author source decals and ground materials; -GroundOnly limits the update.

Loads the saved Flanders World, retains material identities and existing grass
output subgraphs, and saves only the affected materials/textures. Original scene
geometry, source manifest, and World bytes must remain unchanged.

Run with tools/unreal/Run-WTNativeScript.ps1 -Rendering:
  -PythonScript tools/unreal/update_wt_materials.py
  -ReportArgument MaterialUpdateOutput -EvidenceDirectory <fresh directory>
The native wrapper supplies -SceneSource and the fresh audit directory.
"""
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import traceback

import unreal
sys.path.insert(0, str(Path(__file__).resolve().parent))
import wt_materials as materials
import wt_landdetail
import wt_lc_reflectance
import wt_rendinst_decals as decals
from wt_landclass import validate_landclass_surface
from wt_vertex_opacity import validate_scene_vertex_opacity
from wt_water import water_surface_contract
from verify_wt_materials import asset_file, graph_record, statistics
from wt_material_refine_verify import decal_instance
import wt_ground_microdetails as micro
from wt_height_decals import contract as height_contract, SHADER as HEIGHT_SHADER
from wt_ground_material_verify import height_instance, texture_state


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def grass_subgraph(material):
    edit = unreal.MaterialEditingLibrary
    outputs = [node for node in edit.get_material_expressions(material)
               if isinstance(node, unreal.MaterialExpressionLandscapeGrassOutput)]
    if len(outputs) != 1:
        raise RuntimeError('incremental ground update requires one previously authored grass output')
    pending, seen = list(outputs), {}
    while pending:
        node = pending.pop()
        name = node.get_path_name()
        if name in seen:
            continue
        seen[name] = node
        pending.extend(x for x in edit.get_inputs_for_material_expression(material, node) if x)
    nodes = []
    for name, node in sorted(seen.items()):
        inputs = list(edit.get_inputs_for_material_expression(material, node))
        record = dict(path=name, cls=node.get_class().get_name(),
                      input_paths=[x.get_path_name() if x else None for x in inputs])
        if isinstance(node, unreal.MaterialExpressionCustom):
            record['code_sha256'] = hashlib.sha256(str(node.get_editor_property('code')).encode()).hexdigest()
        if isinstance(node, unreal.MaterialExpressionTextureObjectParameter):
            record['parameter'] = str(node.get_editor_property('parameter_name'))
            texture = node.get_editor_property('texture')
            record['texture'] = texture.get_path_name() if texture else None
        nodes.append(record)
    types = []
    for entry in outputs[0].get_editor_property('grass_types'):
        grass_type = entry.get_editor_property('grass_type')
        types.append(dict(name=str(entry.get_editor_property('name')),
                          grass_type=grass_type.get_path_name() if grass_type else None))
    if not types or len({x['name'] for x in types}) != len(types) or any(not x['grass_type'] for x in types):
        raise RuntimeError('saved grass outputs are incomplete or duplicated')
    return dict(nodes=nodes, grass_types=types)


def update(source_path, output):
    command = unreal.SystemLibrary.get_command_line()
    if re.search(r'(?:^|\s)-ObjectOnly(?:\s|$)', command, re.I):
        if re.search(r'(?:^|\s)-GroundOnly(?:\s|$)', command, re.I):
            raise ValueError('ObjectOnly and GroundOnly are separate authoring scopes')
        from update_wt_object_materials import update as update_objects
        return update_objects(source_path, output, author=True)
    if (re.search(r'(?:^|\s)-(?:nullrhi|noshadercompile)\b', command, re.I)
            or not re.search(r'(?:^|\s)-AllowCommandletRendering\b', command, re.I)):
        raise ValueError('material authoring requires rendering and real shader compilation')
    source_file = Path(source_path).resolve(strict=True)
    package = source_file.parent
    evidence = Path(output).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    before_dir = evidence / 'before'
    before_dir.mkdir()
    report = dict(schema='wt_native_material_refine_update_v1', status='running', complete=False,
        started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        engine=unreal.SystemLibrary.get_engine_version(), command_line=command,
        source_manifest_sha256=digest(source_file),
        scripts_sha256={name:digest(Path(__file__).with_name(name)) for name in
            ('update_wt_materials.py','wt_materials.py','wt_landclass.py','wt_landdetail.py',
             'wt_rendinst_decals.py','wt_material_refine_verify.py','verify_wt_materials.py',
             'wt_vertex_opacity.py','wt_tree_alpha.py','wt_runtime_virtual_texture.py','wt_water.py',
             'wt_height_decals.py','wt_ground_microdetails.py','wt_ground_material_verify.py','wt_lc_reflectance.py')},
        parents=[], instances=[], backups=[], source_manifest_modified=False,
        ground_only=bool(re.search(r'(?:^|\s)-GroundOnly(?:\s|$)',command,re.I)),
        saved_world_modified=False, independent_reload_verified=False,
        rendering_visual_quality_verified=False)
    save(evidence / 'verification.json', report)
    try:
        source = json.loads(source_file.read_text(encoding='utf-8-sig'))
        if source.get('id') != 'wt_vlaanderen' or source.get('schema') != 'rm_scene_map_source_v1':
            raise ValueError('expected the original Flanders source')
        landclass = validate_landclass_surface(package, source)
        landdetail = wt_landdetail.validate(package, source)
        microdetails = micro.validate(package)
        report['source_ground_microdetails'] = dict(definition_sha256=micro.DEFINITION_SHA256,
            proof_sha256=microdetails['proof_sha256'],slot_count=microdetails['slot_count'],
            unique_texture_count=microdetails['unique_texture_count'],slice_size=microdetails['slice_size'],
            uv_scale_per_m=microdetails['uv_scale_per_m'],id_count_scale=microdetails['id_count_scale'],
            adaptation_scope=microdetails['adaptation_scope'])
        report['source_vertex_opacity'] = validate_scene_vertex_opacity(package, source)
        water_source = water_surface_contract(source, package)
        report['source_landdetail'] = dict(definition_sha256=digest(package/wt_landdetail.DEFINITION_FILE),
            verification_sha256=landdetail['verification_sha256'], biome_count=65, detail_texture_count=23)
        reflection_data=landdetail['reflectance']
        report['source_landdetail_reflectance']=dict(
            definition_sha256=digest(package/wt_lc_reflectance.DEFINITION_FILE),
            proof_sha256=reflection_data['proof_sha256'],
            source_swizzle_sha256=reflection_data['swizzle_proof_sha256'],
            texture_count=len(reflection_data['reflectance_names']),channels=reflection_data['channels'],
            adapter=json.loads(wt_lc_reflectance.adapter_metadata(reflection_data)),
            albedo_to_reflectance=reflection_data['albedo_to_reflectance'])
        ground_shaders = {HEIGHT_SHADER,'land_mesh','land_mesh_combined_detailed_decal'}
        ground_specs = [spec for spec in source['materials'] if spec['shader'] in ground_shaders]
        height_specs = [spec for spec in ground_specs if spec['shader']==HEIGHT_SHADER]
        decal_specs = [spec for spec in source['materials'] if spec['shader'] in decals.SHADERS]
        if len(decal_specs)!=197 or len(ground_specs)!=36 or len(height_specs)!=34:
            raise ValueError('original rendinst/height/ground material counts differ')
        specs = ground_specs if report['ground_only'] else decal_specs+ground_specs
        parent_kinds = {materials.material_flavor(spec['shader']) for spec in specs}
        report['selected_parent_kinds'] = sorted(parent_kinds)
        report['source_material_counts'] = dict(height_decal=34,ground_other=1,land=1,rendinst_decals=197)
        for spec in specs:
            if materials.material_id(spec) != spec['id']:
                raise ValueError('original material identity differs')
            if spec['shader'] in decals.SHADERS:
                decals.contract(spec)
            elif spec['shader']==HEIGHT_SHADER:
                height_contract(spec,microdetails)
        folder = '/Game/RMSim/Maps/' + source['id']
        world_path = folder + '/World'
        world_file = Path(unreal.Paths.project_content_dir()).resolve() / ('RMSim/Maps/'+source['id']+'/World.umap')
        report['world_sha256_before'] = digest(world_file)
        unreal.EditorLoadingAndSavingUtils.load_map(world_path)
        world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        if world is None or world.get_path_name().split('.',1)[0] != world_path:
            raise RuntimeError('the saved Flanders World did not load')
        terrain_path = folder + '/Materials/WT_Terrain'
        if not unreal.EditorAssetLibrary.does_asset_exist(terrain_path):
            raise RuntimeError('incremental update requires the original saved terrain material')
        terrain = unreal.load_asset(terrain_path)
        grass_before = grass_subgraph(terrain)
        if len(grass_before['grass_types'])!=16:
            raise RuntimeError('ground-only refinement requires the current sixteen saved grass layers')
        report['retained_grass_subgraph'] = grass_before

        extraction = Path(source['extraction_root'])
        if not extraction.is_absolute():
            extraction = (package / extraction).resolve()
        inventory_file = sorted(extraction.glob('textures-*/result.json'))[-1]
        inventory = {item['name']:item for item in json.loads(inventory_file.read_text(encoding='utf-8'))['exported']}
        report['source_texture_inventory_sha256'] = digest(inventory_file)
        changed = {folder+'/Materials/WT_Parent_'+kind for kind in parent_kinds}
        changed.add(terrain_path)
        changed.update(folder+'/Materials/'+spec['id'] for spec in specs)
        texture_keys = {(name,True,False) for name in landdetail['table']['albedo_names']}
        texture_keys.update((name,False,False) for name in reflection_data['reflectance_names'])
        texture_keys.update({(wt_landdetail.MACRO_NAME,True,False),(wt_landdetail.INDEX_NAME,False,True),
                             (wt_landdetail.FLOW_NAME,False,False)})
        texture_keys.update((entry['name'],entry['srgb'],entry['nearest']) for entry in landclass['textures'].values())
        texture_keys.update({('_DefaultMidGray',False,False),('_DefaultWhite',True,False),
                             ('_DefaultNormalAG',False,False),('_DefaultBlack',False,False)})
        texture_keys.update((slot['name'],False,False) for slot in microdetails['slots'])
        for spec in specs:
            if spec['shader'] not in decals.SHADERS:
                for index,raw in enumerate(spec['texture_slots']):
                    if raw:
                        texture_keys.add((raw.split('*')[0].split('$')[0],index in (0,3,5),False))
        for spec in (item for item in specs if item['shader'] in decals.SHADERS):
            kind = decals.flavor(spec['shader'])
            for index in ((0,) if kind=='DecalModulate' else (2,) if kind=='DecalNormal' else (0,2)):
                raw = spec['texture_slots'][index]
                if raw:
                    texture_keys.add((raw.split('*')[0].split('$')[0],index==0 and kind=='DecalDiffuse',False))
        for name,color,nearest in texture_keys:
            asset_name = re.sub('[^A-Za-z0-9_]','_',name)+('_C' if color else '_D')+('_Point' if nearest else '')
            changed.add(folder+'/Textures/'+asset_name)
        changed.add(folder+'/Textures/'+wt_landdetail.ARRAY_NAME)
        changed.add(folder+'/Textures/'+wt_lc_reflectance.ARRAY_NAME)
        changed.add(folder+'/Textures/'+micro.ARRAY_NAME)
        for path in sorted(changed):
            filename = asset_file(path)
            item = dict(asset_package=path, existed_before=filename.is_file(),
                        sha256_before=digest(filename) if filename.is_file() else None)
            if filename.is_file():
                destination=before_dir/('Content/'+path[6:]+'.uasset')
                destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(filename,destination)
            report['backups'].append(item)
        provenance = package / 'material-import-report.json'
        provenance_before = digest(provenance)
        shutil.copy2(provenance,before_dir/provenance.name)
        report['material_report_sha256_before'] = provenance_before
        save(evidence/'verification.json',report)

        report['height_texture_encoding_before'] = {}
        for spec in height_specs:
            for index,color in ((0,True),(2,False)):
                name=spec['texture_slots'][index].split('*')[0].split('$')[0]
                asset_name=re.sub('[^A-Za-z0-9_]','_',name)+('_C' if color else '_D')
                path=folder+'/Textures/'+asset_name
                if path not in report['height_texture_encoding_before']:
                    if not unreal.EditorAssetLibrary.does_asset_exist(path):
                        raise RuntimeError('incremental source height texture is absent: '+path)
                    report['height_texture_encoding_before'][path]=texture_state(unreal.load_asset(path))
        save(evidence/'verification.json',report)
        textures = materials.Textures(package,extraction,folder,landclass,water_source)
        textures.height_decal_source_verified = True
        parent_objects={}
        for kind in sorted(parent_kinds):
            parent=materials.parent_material(folder,kind,textures)
            parent_objects[kind]=parent
            item=dict(asset=parent.get_path_name(),
                graph=graph_record(parent,landclass=landclass,landdetail=landdetail,package=package,microdetails=microdetails),
                completed_shader_statistics=statistics(parent),
                file_sha256=digest(asset_file(parent.get_path_name())),status='passed')
            if kind=='Land' and item['completed_shader_statistics']['num_samplers']>16:
                raise RuntimeError('Land shader exceeds the SM5 sampler budget')
            report['parents'].append(item)
            save(evidence/'verification.json',report)
        for spec in specs:
            kind=materials.material_flavor(spec['shader'])
            identity,instance=materials.make_instance(folder,spec,parent_objects[kind],textures)
            if identity != spec['id']:
                raise RuntimeError('incremental material identity changed')
            item=dict(asset=instance.get_path_name(), source_id=identity, adapter=kind,
                parent=instance.get_editor_property('parent').get_path_name(),
                completed_shader_statistics=statistics(instance),
                file_sha256=digest(asset_file(instance.get_path_name())),status='passed')
            if spec['shader'] in decals.SHADERS:
                item['source_rendinst_decal']=decal_instance(instance,spec,package,inventory)
            elif spec['shader']==HEIGHT_SHADER:
                item['source_height_surface']=height_instance(instance,spec,package,inventory,microdetails)
            report['instances'].append(item)
            save(evidence/'verification.json',report)
        terrain=materials.terrain_material(folder,textures,preserve_grass=True)
        if grass_subgraph(terrain) != grass_before:
            raise RuntimeError('incremental ground update changed the saved grass output subgraph')
        report['parents'].append(dict(asset=terrain.get_path_name(),
            graph=graph_record(terrain,landclass=landclass,landdetail=landdetail,package=package,microdetails=microdetails),
            completed_shader_statistics=statistics(terrain),
            file_sha256=digest(asset_file(terrain.get_path_name())),status='passed'))
        if report['parents'][-1]['completed_shader_statistics']['num_samplers']>16:
            raise RuntimeError('Terrain shader exceeds the SM5 sampler budget')
        unreal.SystemLibrary.execute_console_command(None,'Editor.AsyncTextureCompilationFinishAll')
        report['textures']=textures.imported

        if digest(provenance) != provenance_before:
            raise RuntimeError('material provenance changed concurrently')
        data=json.loads(provenance.read_text(encoding='utf-8'))
        records={item['id']:item for item in data['materials']}
        for spec in specs:
            record=records[spec['id']]
            record.update(adapter=materials.material_flavor(spec['shader']),
                          status='authored_renderer_adapter',unresolved='')
            if spec['shader'] in decals.SHADERS:
                record['source_rendinst_decal']=decals.contract(spec)
            elif spec['shader']==HEIGHT_SHADER:
                record['source_height_surface']=height_contract(spec,microdetails)
        data.update(version=materials.VERSION,source_landdetail=report['source_landdetail'],
            source_ground_microdetails=report['source_ground_microdetails'],
            source_landdetail_reflectance=report['source_landdetail_reflectance'],
            material_refine_update=dict(source_manifest_sha256=report['source_manifest_sha256'],
                original_material_report_sha256=provenance_before,
                update_script_sha256=report['scripts_sha256']['update_wt_materials.py'],
                ground_only=report['ground_only'],height_decal_count=len(height_specs),
                rendinst_decal_count=sum(item['shader'] in decals.SHADERS for item in specs),
                retained_grass_layers=len(grass_before['grass_types'])))
        data['limitations']=[text for text in data.get('limitations',[])
                             if not any(text.startswith(prefix) for prefix in
                                ('terrain uses ','land_mesh ','outer land_mesh ','ground decal geometry writes ',
                                 'Terrain and core LC1 ','Other landclass details, packed reflectance',
                                 'HeightDecal shipped T2 reflection ','Specular transports '))]
        data['limitations'].extend([
            'Ground decal geometry projects source color/opacity through RVT; rendinst diffuse/normal use native mesh DeferredDecal and modulate2x uses Surface/Unlit/Modulate.',
            'Rendinst modulate2x retains linear factor/neutral and source depth/camera offset through Surface Unlit Modulate; it multiplies lit scene color.',
            'Original decal smoothness/metalness multiplication, independent diffuse normal opacity, per-section draw_order and palette painting remain unadapted.',
            'Terrain and core LC1 use 65 original biomes, 23 albedo-height textures and 23 cooked reflection textures; self-coloring, smoothness and winning microdetail IDs follow source rules. Normals remain an explicit UE local alpha-gradient adaptation.',
            'Other landclass detail families, wetness and vertical-cliff shading remain separate.',
            'HeightDecal cooked T2 uses R smoothness/G reflectance/B AO/A microdetail ID, bound to the same-shader CDK BARG rule and actual exporter. Missing source normal uses the source alpha-gradient formula with explicit UE strength0.2; 18 original RGB/alpha shapes have source-id/PNG-bound polarity adapters.',
            'Original 12-slot/11-texture microdetails are a source-backed UE post-RVT adaptation; the shipped land consumer is absent from the pinned public checkout.',
            'Specular transports a coverage-weighted RVT ID payload. LC and RVT IDs retain separate floor/next microdetail samples before result blending; cooked G reflectance/B AO, independent attribute opacity and full correct-bilinear ID gather remain outside this adapter.'])
        data['limitations']=list(dict.fromkeys(data['limitations']))
        save(provenance,data)
        report['material_report_sha256_after']=digest(provenance)
        for item in report['backups']:
            filename=asset_file(item['asset_package'])
            item['sha256_after']=digest(filename) if filename.is_file() else None
        micro.validate(package)
        report['source_manifest_sha256_after']=digest(source_file)
        report['world_sha256_after']=digest(world_file)
        report['source_manifest_modified']=report['source_manifest_sha256_after']!=report['source_manifest_sha256']
        report['saved_world_modified']=report['world_sha256_after']!=report['world_sha256_before']
        if report['source_manifest_modified'] or report['saved_world_modified']:
            raise RuntimeError('material-only update changed the original scene source or saved World')
        report.update(status='passed',complete=True,actual_asset_counts=dict(parents=len(report['parents']),
            instances=len(report['instances']),total=len(report['parents'])+len(report['instances'])),
            checks=dict(original_source_manifest_unchanged=True,original_world_unchanged=True,
                stable_material_identities=True,retained_grass_output_subgraph=True,
                native_material_graphs=True,completed_native_shader_statistics=True,
                source_height_texture_encoding=True,source_twelve_slot_array=True,
                actual_normal_and_payload_output_pins=True,source_lc23_reflection_array=True,
                source_lc_roughness_id_valid_output_pins=True),
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        unreal.log('WT_MATERIAL_REFINE_UPDATE_PASSED '+str(evidence/'verification.json'))
    except Exception as error:
        report.update(status='failed',complete=True,error=str(error),traceback=traceback.format_exc(),
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        unreal.log_error('WT_MATERIAL_REFINE_UPDATE_FAILED '+str(error))
        raise
    finally:
        save(evidence/'verification.json',report)
    return report


if __name__=='__main__':
    command=unreal.SystemLibrary.get_command_line()
    source_arg=re.search(r'-SceneSource=(?:"([^"]+)"|(\S+))',command)
    output_arg=re.search(r'-MaterialUpdateOutput=(?:"([^"]+)"|(\S+))',command)
    if not source_arg or not output_arg:
        raise ValueError('pass -SceneSource=<scene-source.json> -MaterialUpdateOutput=<fresh directory>')
    update(source_arg.group(1) or source_arg.group(2),output_arg.group(1) or output_arg.group(2))