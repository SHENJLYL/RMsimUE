"""Read-only saved Ground40 gate; source graphs and real RHI compilation."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import traceback

import unreal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_wt_materials import asset_file, graph_record, statistics, landclass_texture_record
from wt_materials import material_flavor, material_id
from wt_vertex_opacity import HEIGHT_DECAL_SHADER, validate_scene_vertex_opacity
from wt_landclass import validate_landclass_surface
from wt_ground_material_verify import height_instance, texture_state
import wt_landdetail
import wt_lc_reflectance
import wt_ground_microdetails


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def argument(name):
    command = unreal.SystemLibrary.get_command_line()
    found = re.search(r'(?:^|\s)-' + re.escape(name) + r'=(?:"([^"]+)"|(\S+))', command, re.I)
    require(found is not None, 'missing required argument: ' + name)
    return found.group(1) or found.group(2)


def verify(source_file, output):
    source_file = Path(source_file).resolve(strict=True)
    source_before = digest(source_file)
    source = json.loads(source_file.read_text(encoding='utf-8-sig'))
    require(source.get('id') == 'wt_vlaanderen' and source.get('schema') == 'rm_scene_map_source_v1',
            'expected the original Flanders scene source')
    evidence = Path(output).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    command = unreal.SystemLibrary.get_command_line()
    require(not re.search(r'(?:^|\s)-(?:NullRHI|NoShaderCompile)\b', command, re.I)
            and re.search(r'(?:^|\s)-AllowCommandletRendering\b', command, re.I),
            'Ground40 requires a real RHI and enabled native shader compilation')
    package = source_file.parent
    ue_folder = '/Game/RMSim/Maps/' + source['id']
    content = Path(unreal.Paths.project_content_dir()).resolve() / ('RMSim/Maps/' + source['id'])
    world_file = content / 'World.umap'
    require(world_file.is_file(), 'saved Flanders World is absent')
    protected = {world_file}
    for directory in ('Materials', 'Textures', 'Foliage'):
        protected.update((content / directory).rglob('*.uasset'))
    before = {str(path): digest(path) for path in sorted(protected)}
    report = dict(schema='wt_saved_ground_material_audit_v1', status='running', complete=False,
        started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        engine=unreal.SystemLibrary.get_engine_version(), command_line=command,
        scope='Four saved parents and 36 original ground instances; LC23/Height34/RVT12 source contracts',
        native_assets_written=[], independent_reload=True, rendering_visual_quality_verified=False,
        source_manifest_sha256=source_before, world_sha256_before=before[str(world_file)],
        parents=[], instances=[], protected_file_count=len(before),
        scripts_sha256={name:digest(Path(__file__).with_name(name)) for name in
            ('verify_wt_ground_materials.py','verify_wt_materials.py','wt_materials.py',
             'wt_landclass.py','wt_landdetail.py','wt_lc_reflectance.py',
             'wt_height_decals.py','wt_ground_microdetails.py','wt_ground_material_verify.py',
             'wt_material_refine_verify.py','wt_vertex_opacity.py')})
    save(evidence / 'verification.json', report)
    try:
        report['source_vertex_opacity'] = validate_scene_vertex_opacity(package, source)
        landclass = validate_landclass_surface(package, source)
        detail = wt_landdetail.validate(package, source)
        micro = wt_ground_microdetails.validate(package)
        shaders = {HEIGHT_DECAL_SHADER, 'land_mesh', 'land_mesh_combined_detailed_decal'}
        specs = [item for item in source['materials'] if item['shader'] in shaders]
        require(len(specs) == 36 and sum(s['shader'] == HEIGHT_DECAL_SHADER for s in specs) == 34,
                'original Ground40 material set differs')
        require(all(material_id(s) == s['id'] for s in specs), 'ground material source identity differs')
        extraction = Path(source['extraction_root'])
        if not extraction.is_absolute():
            extraction = (package / extraction).resolve()
        inventory_file = sorted(extraction.glob('textures-*/result.json'))[-1]
        inventory = {item['name']:item for item in json.loads(inventory_file.read_text(encoding='utf-8-sig'))['exported']}
        report['source_texture_inventory_sha256'] = digest(inventory_file)
        report['source_lc_reflection_definition_sha256'] = digest(package / wt_lc_reflectance.DEFINITION_FILE)
        report['source_microdetail_definition_sha256'] = digest(package / 'ground-microdetails/definition.json')
        parents = ['WT_Parent_' + kind for kind in sorted({material_flavor(s['shader']) for s in specs})] + ['WT_Terrain']
        require(len(parents) == 4, 'expected four saved ground parent graphs')
        parent_objects = {}
        for name in parents:
            path = ue_folder + '/Materials/' + name
            require(unreal.EditorAssetLibrary.does_asset_exist(path), 'saved ground parent absent: ' + path)
            material = unreal.load_asset(path)
            require(isinstance(material, unreal.Material), 'ground parent class differs: ' + path)
            item = dict(asset=material.get_path_name(), status='running', file_sha256=digest(asset_file(path)))
            report['parents'].append(item)
            item['graph'] = graph_record(material, landclass=landclass, landdetail=detail,
                                         package=package, microdetails=micro)
            errors = unreal.MaterialEditingLibrary.recompile_material(material)
            require(errors is not None and not isinstance(errors, (str, bool)),
                    'native RecompileMaterial did not return its actual error array')
            item['recompile_errors'] = [str(error) for error in errors]
            require(not item['recompile_errors'], 'saved ground parent shader compilation failed: ' + name)
            item['completed_shader_statistics'] = statistics(material)
            if name in ('WT_Parent_Land', 'WT_Terrain'):
                require(item['completed_shader_statistics']['num_samplers'] <= 16,
                        'ground parent exceeds the native SM5 sampler budget')
            item['file_sha256_after'] = digest(asset_file(path))
            require(item['file_sha256_after'] == item['file_sha256'], 'read-only parent gate changed its asset')
            item['status'] = 'passed'
            parent_objects[name] = material
            save(evidence / 'verification.json', report)
        for spec in specs:
            path = ue_folder + '/Materials/' + spec['id']
            require(unreal.EditorAssetLibrary.does_asset_exist(path), 'saved ground instance absent: ' + path)
            material = unreal.load_asset(path)
            parent_name = 'WT_Parent_' + material_flavor(spec['shader'])
            require(isinstance(material, unreal.MaterialInstanceConstant)
                    and material.get_editor_property('parent') == parent_objects[parent_name],
                    'source ground instance class or parent differs: ' + path)
            item = dict(asset=material.get_path_name(), source_id=spec['id'], source_shader=spec['shader'],
                        parent=parent_objects[parent_name].get_path_name(), file_sha256=digest(asset_file(path)))
            if spec['shader'] == HEIGHT_DECAL_SHADER:
                item['source_height_surface'] = height_instance(material, spec, package, inventory, micro)
            elif spec['shader'] == 'land_mesh':
                item['texture_bindings'] = {
                    name:landclass_texture_record(
                        unreal.MaterialEditingLibrary.get_material_instance_texture_parameter_value(material, name),
                        entry, ue_folder + '/Materials/')
                    for name,entry in landclass['textures'].items()}
            else:
                raw = spec['texture_slots'][0]
                if raw:
                    name = raw.split('*')[0].split('$')[0]
                    texture = unreal.MaterialEditingLibrary.get_material_instance_texture_parameter_value(material, 'T0')
                    expected = ue_folder + '/Textures/' + name + '_C.' + name + '_C'
                    require(texture and texture.get_path_name() == expected
                            and texture.get_editor_property('srgb'),
                            'original other-ground diffuse texture binding differs')
                    entry = inventory[name]
                    require(digest(package / 'textures' / (name + '.png')) == entry['sha256'],
                            'original other-ground diffuse PNG bytes differ')
                    item['diffuse_binding'] = dict(source_png_sha256=entry['sha256'], **texture_state(texture))
            item['completed_shader_statistics'] = statistics(material)
            item['file_sha256_after'] = digest(asset_file(path))
            require(item['file_sha256_after'] == item['file_sha256'], 'read-only instance gate changed its asset')
            item['status'] = 'passed'
            report['instances'].append(item)
            save(evidence / 'verification.json', report)
        require(len(report['parents']) == 4 and len(report['instances']) == 36
                and len({x['asset'] for x in report['parents'] + report['instances']}) == 40,
                'independent ground target count or identity differs')
        after = {path:digest(path) for path in before}
        require(after == before, 'read-only Ground40 changed a protected saved asset')
        require(digest(source_file) == source_before, 'read-only Ground40 changed source manifest bytes')
        save(evidence / 'protected-files.json', before)
        report.update(status='passed', complete=True, actual_asset_counts=dict(parents=4, instances=36, total=40),
            saved_assets_modified=False, protected_files_unchanged=True,
            protected_files_sha256=digest(evidence / 'protected-files.json'),
            source_manifest_sha256_after=digest(source_file), world_sha256_after=digest(world_file),
            checks=dict(source_identity=True, saved_graphs_and_real_output_pins=True,
                native_shader_compilation=True, native_sm5_sampler_budget=True,
                original_lc23_reflection_array=True, original_microdetail12_array=True,
                all34_height_source_contracts=True, saved_assets_world_and_source_unchanged=True),
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        unreal.log('WT_SAVED_GROUND_MATERIAL_AUDIT_PASSED ' + str(evidence / 'verification.json'))
    except Exception as error:
        report.update(status='failed', complete=True, error=str(error), traceback=traceback.format_exc(),
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        unreal.log_error('WT_SAVED_GROUND_MATERIAL_AUDIT_FAILED ' + str(error))
        raise
    finally:
        report['protected_files_changed'] = [
            path for path,wanted in before.items() if not Path(path).is_file() or digest(path) != wanted]
        report['world_sha256_after'] = digest(world_file) if world_file.is_file() else None
        report['source_manifest_sha256_after'] = digest(source_file)
        save(evidence / 'verification.json', report)
    return report


if __name__ == '__main__':
    verify(argument('SceneSource'), argument('GroundMaterialAuditOutput'))
