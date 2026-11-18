"""Update only the original Flanders pond material and its dedicated parent.

Run serially with a real RHI:
  UnrealEditor-Cmd RMSim.uproject -run=pythonscript -script=<this file>
    -AllowCommandletRendering -d3d11 -NoShaderWorker -unattended -nop4 -nosplash
    -SceneSource=<scene-source.json> -WaterUpdateOutput=<fresh directory>

The original World and source package are not reimported. Existing material
files and the material provenance report are backed up before authoring.
"""
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import traceback
from types import SimpleNamespace

import unreal
sys.path.insert(0, str(Path(__file__).resolve().parent))
from wt_water import water_surface_contract, MATERIAL_ID, COLOR_RGBA
from wt_materials import parent_material, make_instance, VERSION
from verify_wt_materials import asset_file, graph_record, statistics


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def update(source_path, output):
    command = unreal.SystemLibrary.get_command_line()
    if (re.search(r'(?:^|\s)-(?:nullrhi|noshadercompile)\b', command, re.I)
            or not re.search(r'(?:^|\s)-AllowCommandletRendering\b', command, re.I)):
        raise ValueError('water authoring requires real shader compilation and -AllowCommandletRendering')
    source_file = Path(source_path).resolve(strict=True)
    evidence = Path(output).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    before_dir = evidence / 'before'
    before_dir.mkdir()
    report = dict(schema='wt_native_water_update_v1', status='running', complete=False,
        started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        engine=unreal.SystemLibrary.get_engine_version(), command_line=command,
        scope='one_original_water_instance_and_one_dedicated_parent',
        source_manifest_sha256=digest(source_file),
        scripts_sha256={name:digest(Path(__file__).with_name(name)) for name in
            ('update_wt_water.py','wt_water.py','wt_materials.py','verify_wt_materials.py')},
        assets=[], source_manifest_modified=False, saved_world_modified=False,
        independent_reload_verified=False, rendering_visual_quality_verified=False)
    save(evidence / 'verification.json', report)
    try:
        source = json.loads(source_file.read_text(encoding='utf-8-sig'))
        contract = water_surface_contract(source, source_file.parent)
        if contract is None:
            raise ValueError('expected the original Flanders pond source')
        report['source_water_surface'] = contract
        folder = '/Game/RMSim/Maps/' + source['id']
        world = Path(unreal.Paths.project_content_dir()).resolve() / 'RMSim' / 'Maps' / source['id'] / 'World.umap'
        world_before = digest(world)
        report['world_sha256_before'] = world_before
        parent_path = folder + '/Materials/WT_Parent_Water'
        instance_path = folder + '/Materials/' + MATERIAL_ID
        if not unreal.EditorAssetLibrary.does_asset_exist(instance_path):
            raise ValueError('original pond instance must already be imported')
        for path in (parent_path, instance_path):
            filename = asset_file(path)
            exists = filename.exists()
            item = dict(asset_package=path, existed_before=exists,
                        file_sha256_before=digest(filename) if exists else None)
            if exists:
                shutil.copy2(filename, before_dir / filename.name)
            report['assets'].append(item)
        provenance = source_file.parent / 'material-import-report.json'
        provenance_before = digest(provenance) if provenance.exists() else None
        report['material_report_sha256_before'] = provenance_before
        if provenance.exists():
            shutil.copy2(provenance, before_dir / provenance.name)
        save(evidence / 'verification.json', report)

        textures = SimpleNamespace(source={}, water_source=contract)
        parent = parent_material(folder, 'Water', textures)
        spec = next(item for item in source['materials'] if item['id'] == MATERIAL_ID)
        identity, instance = make_instance(folder, spec, parent, textures)
        if identity != MATERIAL_ID:
            raise RuntimeError('updated water identity changed')
        report['assets'][0]['graph'] = graph_record(parent)
        for item, material in zip(report['assets'], (parent, instance)):
            item['completed_shader_statistics'] = statistics(material, texture_free=True)
            item['file_sha256_after'] = digest(asset_file(item['asset_package']))
            item['asset'] = material.get_path_name()
        actual_parent = instance.get_editor_property('parent')
        if actual_parent.get_path_name() != parent.get_path_name():
            raise RuntimeError('pond material instance parent did not update')
        value = unreal.MaterialEditingLibrary.get_material_instance_vector_parameter_value(instance, 'SourceWaterColor')
        rgba = [float(value.get_editor_property(channel)) for channel in ('r','g','b','a')]
        if any(abs(a-b)>1e-7 for a,b in zip(rgba, COLOR_RGBA)):
            raise RuntimeError('pond material did not retain original level RGB')
        report['actual_color_rgba'] = rgba

        if provenance.exists():
            if digest(provenance) != provenance_before:
                raise RuntimeError('material provenance changed concurrently')
            data = json.loads(provenance.read_text(encoding='utf-8'))
            records = [entry for entry in data['materials'] if entry['id'] == MATERIAL_ID]
            if len(records) != 1:
                raise RuntimeError('material provenance has no unique original pond entry')
            records[0].update(adapter='Water', status='authored_renderer_adapter', unresolved='',
                              source_water_surface=contract)
            data.update(version=VERSION, source_water_surface=contract)
            data['water_material_update'] = dict(source_manifest_sha256=report['source_manifest_sha256'],
                original_material_report_sha256=provenance_before, update_script_sha256=report['scripts_sha256']['update_wt_water.py'])
            save(provenance, data)
            report['material_report_sha256_after'] = digest(provenance)

        report['source_manifest_sha256_after'] = digest(source_file)
        report['world_sha256_after'] = digest(world)
        report['source_manifest_modified'] = report['source_manifest_sha256_after'] != report['source_manifest_sha256']
        report['saved_world_modified'] = report['world_sha256_after'] != world_before
        if report['source_manifest_modified'] or report['saved_world_modified']:
            raise RuntimeError('water-only update changed the original scene source or saved World')
        report.update(status='passed', complete=True,
            checks=dict(source_identity=True, source_rgb=True, parent_graph=True,
                        real_completed_shader_statistics=True, original_world_unchanged=True,
                        original_source_manifest_unchanged=True),
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        unreal.log('WT_WATER_UPDATE_PASSED ' + str(evidence / 'verification.json'))
    except Exception as error:
        report.update(status='failed', complete=True, error=str(error), traceback=traceback.format_exc(),
                      finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        unreal.log_error('WT_WATER_UPDATE_FAILED ' + str(error))
        raise
    finally:
        save(evidence / 'verification.json', report)
    return report


if __name__ == '__main__':
    command = unreal.SystemLibrary.get_command_line()
    source_arg = re.search(r'-SceneSource=(?:"([^"]+)"|(\S+))', command)
    output_arg = re.search(r'-WaterUpdateOutput=(?:"([^"]+)"|(\S+))', command)
    if not source_arg or not output_arg:
        raise ValueError('pass -SceneSource=<scene-source.json> -WaterUpdateOutput=<fresh directory>')
    update(source_arg.group(1) or source_arg.group(2), output_arg.group(1) or output_arg.group(2))
