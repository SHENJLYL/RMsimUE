"""UE commandlet: add source foliage to the saved scene and bake its weight maps.

Run through Author-WTGrass.ps1 with a rendering RHI. The existing imported
geometry/heightfield and source identity remain unchanged.
"""
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import time
import traceback

import unreal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wt_grass import install_grass, validate_foliage


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, record):
    temp = path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
    temp.replace(path)


def argument(name):
    match = re.search(r'-'+name+r'=(?:"([^"]+)"|(\S+))', unreal.SystemLibrary.get_command_line())
    return (match.group(1) or match.group(2)) if match else None


def grass_asset_hashes(content, folder):
    disk = content / folder.removeprefix('/Game/')
    foliage = disk / 'Foliage'
    terrain = disk / 'Materials' / 'WT_Terrain.uasset'
    if not foliage.is_dir() or not terrain.is_file():
        raise ValueError('resume needs saved Foliage assets and Terrain')
    suffixes = {'.uasset', '.uexp', '.ubulk'}
    files = [p for p in foliage.rglob('*') if p.is_file() and p.suffix.lower() in suffixes]
    files += [p for p in terrain.parent.glob('WT_Terrain.*') if p.is_file() and p.suffix.lower() in suffixes]
    if not any(p.suffix.lower() == '.uasset' for p in files if p != terrain):
        raise ValueError('saved Foliage asset inventory is empty')
    return {str(p.resolve()): sha(p) for p in sorted(set(files))}


def check_resume_assets(content, folder, report, phase):
    evidence = report['resume_validation']
    after = grass_asset_hashes(content, folder)
    evidence['asset_sha256_'+phase] = after
    evidence['saved_assets_unchanged_'+phase] = after == evidence['asset_sha256_before']
    if after != evidence['asset_sha256_before']:
        raise ValueError('resume changed a saved Foliage or Terrain asset during '+phase)
    if sha(evidence['authored_report_file']) != evidence['authored_report_sha256']:
        raise ValueError('authored grass report changed during resume')


def resume_authored_grass(checked, package, folder, content, landscape):
    authored_file = package / 'ue-grass-import-report.json'
    authored = read(authored_file)
    expected = dict(definition_sha256=checked['definition_sha256'],
        source_level_sha256=checked['definition']['source_level_sha256'],
        importer_sha256=sha(Path(__file__).with_name('wt_grass.py')))
    if authored.get('schema') != 'wt_grass_native_import_v1' or authored.get('status') != 'authored_pending_weight_bake':
        raise ValueError('resume requires the completed source grass authoring report')
    if any(authored.get(key) != value for key, value in expected.items()):
        raise ValueError('authored grass source definition, level or importer SHA differs')
    if len(authored.get('source_models', [])) != len(checked['models']) or len(authored.get('grass_types', [])) != len(checked['layers']):
        raise ValueError('resume model/layer inventory differs from the bound foliage contract')
    evidence = dict(status='saved_assets_verified', authored_report_file=str(authored_file),
        authored_report_sha256=sha(authored_file), **expected)
    root = folder + '/Foliage'
    terrain_path = folder+'/Materials/WT_Terrain.WT_Terrain'
    if authored.get('terrain') != terrain_path:
        raise ValueError('authored Terrain path differs from the source map')
    terrain = landscape.get_editor_property('landscape_material')
    if not isinstance(terrain, unreal.Material) or terrain.get_path_name() != terrain_path:
        raise ValueError('saved Landscape does not use the authored source Terrain')
    edit = unreal.MaterialEditingLibrary
    expressions = list(edit.get_material_expressions(terrain))
    outputs = [node for node in expressions if isinstance(node, unreal.MaterialExpressionLandscapeGrassOutput)]
    if len(outputs) != 1:
        raise ValueError('saved Terrain must contain exactly one LandscapeGrassOutput')
    output = outputs[0]
    grass_inputs = list(output.get_editor_property('grass_types'))
    names = [str(item.get_editor_property('name')) for item in grass_inputs]
    if len(names) != len(checked['layers']) or len(set(names)) != len(names) or set(names) != set(checked['layers']):
        raise ValueError('saved Terrain grass names are missing, duplicated or differ from source')
    output_names = [str(name) for name in edit.get_material_expression_input_names(output)]
    input_nodes = list(edit.get_inputs_for_material_expression(terrain, output))
    if output_names != names or len(input_nodes) != len(names) or any(node is None for node in input_nodes):
        raise ValueError('saved Terrain grass output is disconnected')
    evidence['terrain'] = terrain_path
    evidence['grass_output'] = output.get_path_name()
    evidence['layers'] = []
    for item, node in zip(grass_inputs, input_nodes):
        name = str(item.get_editor_property('name'))
        source = checked['layers'][name]
        grass_type = item.get_editor_property('grass_type')
        type_path = root+'/Types/WT_'+name+'.WT_'+name
        if not isinstance(grass_type, unreal.LandscapeGrassType) or grass_type.get_path_name() != type_path:
            raise ValueError('saved GrassType binding differs: '+name)
        varieties = list(grass_type.get_editor_property('grass_varieties'))
        if len(varieties) != 1:
            raise ValueError('saved GrassType variety count differs: '+name)
        model = varieties[0].get_editor_property('grass_mesh')
        overrides = list(varieties[0].get_editor_property('override_materials'))
        model_path = root+'/Meshes/'+source['model_id']+'.'+source['model_id']
        material_path = root+'/Materials/'+name+'.'+name
        if model is None or model.get_path_name() != model_path or len(overrides) != 1 or (
                overrides[0] is None or overrides[0].get_path_name() != material_path):
            raise ValueError('saved grass model or layer material override differs: '+name)
        channel = source['weight_channel']
        if not isinstance(node, unreal.MaterialExpressionComponentMask) or any(
                bool(node.get_editor_property(field)) != (index == channel)
                for index, field in enumerate(('r','g','b','a'))):
            raise ValueError('saved grass weight channel differs: '+name)
        evidence['layers'].append(dict(name=name, grass_type=type_path, mesh=model_path,
            material=material_path, weight_channel=channel, output_input=node.get_path_name()))
    evidence['meshes'] = []
    for name, source in checked['models'].items():
        path = root+'/Meshes/'+name+'.'+name
        if not unreal.EditorAssetLibrary.does_asset_exist(path):
            raise ValueError('saved source grass mesh is missing: '+name)
        mesh = unreal.load_asset(path)
        if not isinstance(mesh, unreal.StaticMesh) or (
                unreal.EditorAssetLibrary.get_metadata_tag(mesh,'RMSimGrassSourceSHA256') != source['sha256']):
            raise ValueError('saved grass mesh source SHA differs: '+name)
        geometry = json.loads(unreal.MapImportLibrary.describe_map_mesh(mesh))
        box = mesh.get_bounding_box()
        actual = {'lo':[box.min.x,box.min.y,box.min.z], 'hi':[box.max.x,box.max.y,box.max.z]}
        wanted = {edge:[source['bounds_m'][('hi' if edge=='lo' else 'lo') if axis==1 else edge][axis]
                       *(-100 if axis==1 else 100) for axis in range(3)] for edge in ('lo','hi')}
        errors = [abs(actual[edge][axis]-wanted[edge][axis]) for edge in ('lo','hi') for axis in range(3)]
        if geometry.get('triangles') != source['triangles'] or geometry.get('uv_channels') != 1 or (
                not all(error <= .01 for error in errors)):
            raise ValueError('saved grass mesh triangles/UV/bounds differ: '+name)
        evidence['meshes'].append(dict(id=name,asset=path,source_sha256=source['sha256'],
            triangles=geometry['triangles'],uv_channels=geometry['uv_channels'],max_bounds_error_cm=max(errors)))
    evidence['weights'] = []
    if set(authored.get('weight_assets',{})) != set(checked['weights']):
        raise ValueError('authored weight texture inventory differs')
    for name, source in checked['weights'].items():
        path = root+'/Textures/'+name+'.'+name
        if authored['weight_assets'][name] != path or not unreal.EditorAssetLibrary.does_asset_exist(path):
            raise ValueError('saved weight texture path differs: '+name)
        texture = unreal.load_asset(path)
        if not isinstance(texture,unreal.Texture2D) or (
                unreal.EditorAssetLibrary.get_metadata_tag(texture,'RMSimGrassSourceSHA256') != source['sha256']):
            raise ValueError('saved weight texture source SHA differs: '+name)
        nodes = [node for node in expressions if isinstance(node,unreal.MaterialExpressionTextureObjectParameter)
                 and str(node.get_editor_property('parameter_name')) == 'WT_GrassWeight_'+name]
        if len(nodes) != 1 or nodes[0].get_editor_property('texture') is None or (
                nodes[0].get_editor_property('texture').get_path_name() != path):
            raise ValueError('Terrain weight texture binding differs: '+name)
        evidence['weights'].append(dict(id=name,asset=path,source_sha256=source['sha256']))
    parent_path = root+'/Materials/WT_Grass.WT_Grass'
    if authored.get('grass_parent') != parent_path or not unreal.EditorAssetLibrary.does_asset_exist(parent_path):
        raise ValueError('authored grass parent material is missing or differs')
    evidence['asset_sha256_before'] = grass_asset_hashes(content,folder)
    evidence['saved_asset_file_count'] = len(evidence['asset_sha256_before'])
    return authored, evidence


def build_complete_grass(landscape, cameras, report, output):
    """Drain a progressing UE 5.8 synchronous grass-map build, then apply all gates.

    BuildGrassMapsNowForComponents keeps a call-local FailedComponents set while
    NotReady states can return to Pending after lazy grass shaders finish. The
    count-based loop can therefore return with streaming/rendering still active.
    A fresh native call clears that transient set and advances those real jobs.
    Never accept stale maps, retry a stalled count, or rebuild authored assets.
    """
    started = time.monotonic()
    previous_outdated = None
    report['native_bake_attempts'] = []
    report['native_bake_policy'] = dict(max_attempts=3, retry_budget_seconds=300,
        require_strictly_decreasing_outdated=True, fixed_sleep_seconds=0)
    for attempt in range(1, 4):
        native = json.loads(unreal.MapVegetationLibrary.build_landscape_grass(landscape, cameras))
        elapsed = time.monotonic() - started
        report['native_bake'] = native
        fields = ('status', 'landscape_components', 'components_with_valid_data',
            'components_with_weights', 'outdated_grass_maps_after_build',
            'generated_hism_components', 'generated_instances',
            'collision_enabled_grass_components', 'finish_assets_seconds',
            'build_maps_seconds', 'update_instances_seconds')
        record = {field: native.get(field) for field in fields}
        record.update(attempt=attempt, elapsed_seconds=elapsed)
        report['native_bake_attempts'].append(record)
        save(output, report)
        total = native.get('landscape_components')
        outdated = native.get('outdated_grass_maps_after_build')
        if (type(total) is not int or total <= 0 or type(outdated) is not int or
                not 0 <= outdated <= total or native.get('collision_enabled_grass_components') != 0):
            raise RuntimeError('grass bake returned invalid counts or enabled collision')
        if native.get('status') == 'built':
            if outdated != 0 or native.get('components_with_valid_data') != total:
                raise RuntimeError('native grass built status conflicts with stale or invalid data')
            return native
        if native.get('status') != 'incomplete' or outdated == 0:
            raise RuntimeError('native grass bake failed: ' + str(native.get('error', native.get('status'))))
        baseline = total if previous_outdated is None else previous_outdated
        if outdated >= baseline:
            raise RuntimeError('grass maps stopped progressing: outdated=' + str(outdated))
        if attempt == 3 or elapsed >= 300:
            raise RuntimeError('grass maps did not complete within the bounded native build attempts')
        previous_outdated = outdated
        unreal.log('WT_GRASS_BAKE_CONTINUE attempt=' + str(attempt) +
            ' outdated=' + str(outdated) + ' elapsed_s=' + format(elapsed, '.3f'))
    raise RuntimeError('grass bake exhausted its native attempts')


def main():
    resume = bool(re.search(r'(?:^|\s)-ResumeAuthoredAssets(?=\s|$)',
        unreal.SystemLibrary.get_command_line(), re.IGNORECASE))
    package_arg, output_arg = argument('FoliagePackage'), argument('FoliageReport')
    if not package_arg or not output_arg:
        raise ValueError('pass -FoliagePackage and -FoliageReport')
    package, output = Path(package_arg).resolve(strict=True), Path(output_arg).resolve()
    if output.exists():
        raise ValueError('grass verification output must be new')
    output.parent.mkdir(parents=True, exist_ok=True)
    report = dict(schema='wt_grass_native_bake_v1', status='running',
        started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        command=unreal.SystemLibrary.get_command_line(), resume_authored_assets=resume,
        independent_material_104_gate='pending', independent_grass_27_gate='pending',
        scripts_sha256={name: sha(Path(__file__).with_name(name)) for name in ('author_wt_grass.py','wt_grass.py')})
    save(output, report)
    failure = None
    try:
        checked = validate_foliage(package)
        map_descriptor = read(package/'map.json')
        source_file = package/'scene-source.json'
        source_hash = sha(source_file)
        folder = '/Game/RMSim/Maps/'+checked['map_id']
        world_asset = folder+'/World'
        content = Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir())).resolve()
        world_file = content/(world_asset.removeprefix('/Game/')+'.umap')
        import_report_file = package/'ue-scene-import-report.json'
        previous_report = read(import_report_file)
        previous_report_hash = sha(import_report_file)
        report.update(source_manifest_sha256=source_hash, source_sha256=map_descriptor['source_sha256'],
            foliage_definition_sha256=checked['definition_sha256'], world_asset=world_asset,
            map_file_sha256_before=sha(world_file), scene_import_report_sha256_before=previous_report_hash)
        backup = output.parent/'World-before-grass.umap'
        if backup.exists():
            raise ValueError('grass World backup already exists')
        shutil.copyfile(world_file, backup)
        save(output, report)
        world = unreal.EditorLoadingAndSavingUtils.load_map(world_asset)
        if world is None:
            raise RuntimeError('saved source scene could not be loaded')
        settings = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.WorldSettings)
        tags = [str(tag) for tag in settings[0].get_editor_property('tags')] if len(settings) == 1 else []
        if 'RMSimMapId='+checked['map_id'] not in tags or 'RMSimSourceSha256='+map_descriptor['source_sha256'] not in tags:
            raise ValueError('saved World source identity differs from foliage')
        landscape = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Landscape)
        if len(landscape) != 1:
            raise RuntimeError('grass authoring requires the one original Landscape')
        if resume:
            report['authoring'], report['resume_validation'] = resume_authored_grass(
                checked, package, folder, content, landscape[0])
        else:
            report['authoring'] = install_grass(package, folder)
        save(output, report)
        default_spawn = next(item for item in map_descriptor['spawns'] if item['id'] == 'default')
        spawn = default_spawn['position_m']
        cameras = [unreal.Vector(spawn[0]*100, -spawn[1]*100, spawn[2]*100+1500),
                   unreal.Vector(107425, 101175, spawn[2]*100+1500)]
        native = build_complete_grass(landscape[0], cameras, report, output)
        if resume:
            check_resume_assets(content, folder, report, 'after_bake')
        save(output, report)
        if native.get('status') != 'built' or native['generated_instances'] <= 0:
            raise RuntimeError('grass maps or initial source grass instances did not complete')
        expected = {item['id']: item for item in checked['layers'].values()}
        actual = {item['name']: item for item in native['layers']}
        if set(actual) != set(expected):
            raise ValueError('native landscape grass layers differ from source')
        for name, wanted in expected.items():
            varieties = actual[name]['variety_settings']
            if len(varieties) != 1:
                raise ValueError('unexpected native grass variety count')
            got = varieties[0]
            if any(actual[name][field] <= 0 for field in
                   ('weighted_components', 'nonzero_weight_samples', 'weight_byte_sum')):
                raise ValueError('source grass layer has no baked weights: '+name)
            model = wanted['model_id']
            if got['mesh'] != folder+'/Foliage/Meshes/'+model+'.'+model:
                raise ValueError('native grass model binding differs: '+name)
            for axis in ('x', 'z'):
                for endpoint, index in (('min', 0), ('max', 1)):
                    if abs(got['scale_'+axis+'_'+endpoint]-wanted['scale_'+axis][index]) > 1.e-5:
                        raise ValueError('native grass scale differs: '+name)
            if abs(got['density_per_100m2']-wanted['density_per_100m2']) > 0.001 or (
                got['start_cull_cm'] != int(round(wanted['start_cull_distance_cm'])) or
                got['end_cull_cm'] != int(round(wanted['end_cull_distance_cm']))):
                raise ValueError('native grass density/cull settings differ: '+name)
        if sha(source_file) != source_hash or sha(import_report_file) != previous_report_hash:
            raise ValueError('source or import report changed during grass authoring')
        if not unreal.EditorLoadingAndSavingUtils.save_map(world, world_asset):
            raise RuntimeError('grass weight data could not be saved in the World')
        if resume:
            check_resume_assets(content, folder, report, 'after_world_save')
        after = sha(world_file)
        previous_report.setdefault('postprocess', []).append(dict(
            step='source_landscape_grass', verification=str(output),
            foliage_definition_sha256=checked['definition_sha256'],
            map_file_sha256_before=report['map_file_sha256_before'], map_file_sha256_after=after,
            previous_import_report_sha256=previous_report_hash,
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))
        previous_report['map_file_sha256'] = after
        save(import_report_file, previous_report)
        report.update(status='passed', map_file_sha256_after=after,
            scene_import_report_sha256_after=sha(import_report_file), generated_instances=native['generated_instances'],
            grass_layer_count=len(actual), source_geometry_changed=False, vehicle_physics_changed=False)
    except Exception as error:
        failure = error
        report.update(status='failed', error=str(error), traceback=traceback.format_exc())
    finally:
        report['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(output, report)
    if failure is not None:
        raise RuntimeError('Grass authoring failed; see '+str(output)) from failure
    unreal.log('WT_GRASS_BAKED '+str(output))


if __name__ == '__main__':
    main()
