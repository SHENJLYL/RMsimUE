"""Native author/reload gate for the existing Surface parent's layered UV normals.

The author saves one parent. Every source layered instance is read and compiled;
instances, textures, Foliage, Terrain, World and scene-source remain byte-equal.
Root's serial native wrapper owns process/rendering/shader-worker configuration.
"""
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import sys
import traceback

import unreal
sys.path.insert(0, str(Path(__file__).resolve().parent))
import wt_materials as materials
import wt_object_normals as normals
from verify_wt_materials import asset_file, statistics


# GetStatistics uses the completed material resource's GetSamplerUsage. The
# seven Custom texture inputs are separate from that compiled-resource count.
SURFACE_SAMPLER_BUDGET = 10
SAMPLER_BASELINE = dict(
    report='artifacts/maps/wt-refine-20261118/materials-reload-04/audit/verification.json',
    report_sha256='a7e63cc44b231842f5443d74ccca1b61940820f0cae223183fe5029fee288097',
    surface_sha256='1eb9b130aec6cb523afdfa27586397c3d062c6ad9336abbbd3a8bfe393a77a34',
    completed_native_num_samplers=10, source_texture_inputs=7,
    api='MaterialEditingLibrary.cpp:2144 Resource->GetSamplerUsage()')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def require(value, message):
    if not value:
        raise RuntimeError('layered object gate: ' + message)


def load(path):
    require(unreal.EditorAssetLibrary.does_asset_exist(path), 'saved asset missing: ' + path)
    asset = unreal.load_asset(path)
    require(asset is not None, 'saved asset failed to load: ' + path)
    return asset


def f32(value):
    return struct.unpack('<f', struct.pack('<f', float(value)))[0]


def vector(instance, name, expected):
    value = unreal.MaterialEditingLibrary.get_material_instance_vector_parameter_value(instance, name)
    actual = [float(value.get_editor_property(x)) for x in ('r', 'g', 'b', 'a')]
    require(actual == [f32(x) for x in expected], 'existing vector differs: ' + name)
    return actual


class ExistingDefaults:
    """Parent creation uses these saved defaults without importing/saving textures."""
    def __init__(self, folder):
        self.folder = folder

    def get(self, name, color, default='White', **unused):
        require(name is None, 'ObjectOnly unexpectedly requested a texture import')
        key = '_Default' + default + ('_C' if color else '_D')
        texture = load(self.folder + '/Textures/' + key)
        require(isinstance(texture, unreal.Texture2D), 'saved default is not Texture2D')
        return texture


def instance_record(instance, spec, parent, package, inventory, texture_cache):
    edit = unreal.MaterialEditingLibrary
    require(spec['shader'] in normals.SHADERS and materials.material_id(spec) == spec['id'],
            'source material identity or family differs')
    require(instance.get_editor_property('parent') == parent, 'source instance parent differs')
    require(edit.get_material_instance_scalar_parameter_value(instance, 'Family') == 1.0,
            'source layered Family differs')
    params = materials.parameters(spec['parameters'])

    def val(name, default):
        return params.get(name, [default])[0]

    tiles = [val('detail1_tile_u', 1), val('detail1_tile_v', 1),
             val('detail2_tile_u', 1), val('detail2_tile_v', 1)]
    mask = [val('mask_tile_u', 1), val('mask_tile_v', 1),
            (val('mask_gamma_start', .5) + val('mask_gamma_end', 2)) * .5, 0]
    result = dict(source_id=spec['id'], source_shader=spec['shader'],
                  asset=instance.get_path_name(), parent=parent.get_path_name(),
                  family=1.0, tiles=vector(instance, 'Tiles', tiles),
                  mask=vector(instance, 'Mask', mask), texture_bindings={})
    root = parent.get_path_name().rsplit('/Materials/', 1)[0]
    for index in range(7):
        raw = spec['texture_slots'][index]
        color = index in (0, 3, 5)
        fallback = 'White' if index in (0, 1, 3, 5) else 'NormalAG'
        name = raw.split('*')[0].split('$')[0] if raw else '_Default' + fallback
        asset_name = re.sub('[^A-Za-z0-9_]', '_', name) + ('_C' if color else '_D')
        expected = root + '/Textures/' + asset_name + '.' + asset_name
        texture = edit.get_material_instance_texture_parameter_value(instance, 'T' + str(index))
        compression = (unreal.TextureCompressionSettings.TC_DEFAULT if color
                       else unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP)
        require(isinstance(texture, unreal.Texture2D) and texture.get_path_name() == expected,
                'source layered texture binding differs: ' + spec['id'] + ':T' + str(index))
        require(bool(texture.get_editor_property('srgb')) == color
                and texture.get_editor_property('compression_settings') == compression,
                'source layered texture color/data encoding differs: ' + name)
        if expected not in texture_cache:
            entry = inventory[name] if raw else None
            if entry:
                png = package / 'textures' / (name + '.png')
                require(digest(png) == entry['sha256'], 'original PNG differs: ' + name)
            texture_cache[expected] = dict(asset=expected, srgb=color,
                compression=str(compression), source_png_sha256=entry['sha256'] if entry else None,
                saved_asset_sha256=digest(asset_file(expected)))
        result['texture_bindings']['T' + str(index)] = expected
    result['completed_shader_statistics'] = statistics(instance)
    result['file_sha256'] = digest(asset_file(instance.get_path_name()))
    result['status'] = 'passed'
    return result


def update(source_path, output, author=True):
    command = unreal.SystemLibrary.get_command_line()
    require(not re.search(r'(?:^|\s)-(?:nullrhi|noshadercompile)\b', command, re.I)
            and re.search(r'(?:^|\s)-AllowCommandletRendering\b', command, re.I),
            'real rendering and shader compilation are required')
    source_file = Path(source_path).resolve(strict=True)
    package = source_file.parent
    evidence = Path(output).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    report = dict(schema='wt_native_layered_object_normals_v1', status='running', complete=False,
        author=bool(author), started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        engine=unreal.SystemLibrary.get_engine_version(), command_line=command,
        native_assets_written=[], independent_reload=not author, rendering_visual_quality_verified=False,
        source_manifest_sha256=digest(source_file), parents=[], instances=[],
        scripts_sha256={name:digest(Path(__file__).with_name(name)) for name in
                        ('update_wt_object_materials.py','wt_object_normals.py',
                         'wt_materials.py','verify_wt_materials.py')})
    save(evidence/'verification.json', report)
    try:
        source = json.loads(source_file.read_text(encoding='utf-8-sig'))
        require(source['id'] == 'wt_vlaanderen' and source['schema'] == 'rm_scene_map_source_v1',
                'expected the original Flanders source')
        specs = [x for x in source['materials'] if x['shader'] in normals.SHADERS]
        require(len(specs) == 192, 'audited source layered material count differs')
        folder = '/Game/RMSim/Maps/' + source['id']
        parent_path = folder + '/Materials/WT_Parent_Surface'
        parent_file = asset_file(parent_path)
        parent = load(parent_path)
        content = Path(unreal.Paths.project_content_dir()).resolve()
        map_root = content/'RMSim/Maps/wt_vlaanderen'
        world_file = map_root/'World.umap'
        report['world_sha256_before'] = digest(world_file)
        report['parent_sha256_before'] = digest(parent_file)
        texture_assets = set()
        for spec in specs:
            for index, raw in enumerate(spec['texture_slots'][:7]):
                color = index in (0, 3, 5)
                fallback = 'White' if index in (0, 1, 3, 5) else 'NormalAG'
                name = raw.split('*')[0].split('$')[0] if raw else '_Default' + fallback
                asset_name = re.sub('[^A-Za-z0-9_]', '_', name) + ('_C' if color else '_D')
                texture_assets.add(asset_file(folder + '/Textures/' + asset_name))
        for name in ('_DefaultWhite_C','_DefaultWhite_D','_DefaultNormalAG_D'):
            texture_assets.add(asset_file(folder + '/Textures/' + name))
        protected = sorted(set((map_root/'Materials').glob('*.uasset'))
                           | set((map_root/'Foliage').rglob('*.uasset')) | texture_assets)
        if author:
            protected = [p for p in protected if p != parent_file]
        protected_hashes = {str(p):digest(p) for p in protected}
        extraction = Path(source['extraction_root'])
        if not extraction.is_absolute():
            extraction = (package/extraction).resolve()
        inventory_path = sorted(extraction.glob('textures-*/result.json'))[-1]
        inventory = {x['name']:x for x in json.loads(inventory_path.read_text(encoding='utf8'))['exported']}
        report['source_texture_inventory_sha256'] = digest(inventory_path)
        report['source_contract'] = normals.contract(materials.OBJECT_CODE)
        if author:
            before = evidence/'before'
            before.mkdir()
            shutil.copy2(parent_file, before/parent_file.name)
            parent = materials.parent_material(folder, 'Surface', ExistingDefaults(folder), save_asset=False)
        errors = unreal.MaterialEditingLibrary.recompile_material(parent)
        require(errors is not None and not isinstance(errors, (str, bool)),
                'UE 5.8 parent compilation must return Array[str]')
        report['parent_compile_errors'] = [str(error) for error in errors]
        require(not errors, 'completed parent shader compilation failed: ' + '; '.join(report['parent_compile_errors']))
        graph = normals.graph_record(parent)
        native_stats = statistics(parent)
        report['sampler_budget'] = dict(SAMPLER_BASELINE, limit=SURFACE_SAMPLER_BUDGET)
        report['actual_parent_statistics'] = native_stats
        parent_record = dict(asset=parent.get_path_name(), graph=graph,
            completed_shader_statistics=native_stats, file_sha256=digest(parent_file), status='measured')
        report['parents'].append(parent_record)
        save(evidence/'verification.json', report)
        require(native_stats['num_samplers'] <= SURFACE_SAMPLER_BUDGET,
                'Surface sampler usage exceeds the saved native baseline: ' + json.dumps(native_stats))
        parent_record['status'] = 'passed'
        cache = {}
        for spec in specs:
            instance = load(folder+'/Materials/'+spec['id'])
            record = instance_record(instance, spec, parent, package, inventory, cache)
            record['status'] = 'measured'
            report['instances'].append(record)
            require(record['completed_shader_statistics']['num_samplers'] <= SURFACE_SAMPLER_BUDGET,
                    'layered instance sampler usage exceeds the native baseline: ' + json.dumps(record))
            record['status'] = 'passed'
        report['textures'] = list(cache.values())
        for item in report['textures']:
            require(digest(asset_file(item['asset'])) == item['saved_asset_sha256'],
                    'texture changed during the object gate')
        after = {name:digest(name) for name in protected_hashes}
        require(after == protected_hashes, 'an instance, other parent, Terrain or Foliage asset changed')
        report['protected_assets'] = dict(count=len(protected_hashes), unchanged=True,
            files=[dict(file=name,sha256=sha) for name,sha in protected_hashes.items()])
        report['world_sha256_after'] = digest(world_file)
        report['source_manifest_sha256_after'] = digest(source_file)
        require(report['world_sha256_after'] == report['world_sha256_before']
                and report['source_manifest_sha256_after'] == report['source_manifest_sha256'],
                'World or original source changed')
        if author:
            report['native_asset_save_attempted'] = parent.get_path_name()
            require(unreal.EditorAssetLibrary.save_loaded_asset(parent), 'failed to save validated Surface parent')
            report['native_assets_written'] = [parent.get_path_name()]
        report['parent_sha256_after'] = digest(parent_file)
        parent_record['file_sha256'] = report['parent_sha256_after']
        if not author:
            require(report['parent_sha256_before'] == report['parent_sha256_after'],
                    'read-only audit changed the parent')
        report.update(status='passed', complete=True,
            actual_asset_counts=dict(parents=1, layered_instances=len(specs), total=1+len(specs)),
            checks=dict(source_identity=True, original_texture_bindings=True,
                        per_uv_source_frame=True, source_world_RNM=True,
                        actual_normal_output_connection=True, correct_tangent_space_contract=True,
                        original_instance_and_ground_assets_unchanged=True,
                        completed_native_shader_statistics=True,
                        original_world_and_manifest_unchanged=True),
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        unreal.log('WT_LAYERED_OBJECT_NORMALS_PASSED '+str(evidence/'verification.json'))
    except Exception as error:
        report.update(status='failed', complete=True, error=str(error), traceback=traceback.format_exc(),
                      finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        unreal.log_error('WT_LAYERED_OBJECT_NORMALS_FAILED '+str(error))
        raise
    finally:
        if 'parent_file' in locals() and parent_file.is_file():
            report['parent_sha256_after'] = digest(parent_file)
        if 'world_file' in locals() and world_file.is_file():
            report['world_sha256_after'] = digest(world_file)
        report['source_manifest_sha256_after'] = digest(source_file)
        save(evidence/'verification.json', report)
    return report


if __name__ == '__main__':
    command = unreal.SystemLibrary.get_command_line()
    source = re.search(r'-SceneSource=(?:"([^"]+)"|(\S+))', command)
    output = re.search(r'-ObjectMaterialUpdateOutput=(?:"([^"]+)"|(\S+))', command)
    if not source or not output:
        raise ValueError('pass -SceneSource and -ObjectMaterialUpdateOutput')
    update(source.group(1) or source.group(2), output.group(1) or output.group(2), author=True)
