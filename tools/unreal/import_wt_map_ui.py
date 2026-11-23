"""UE Editor Python: import original WT map fonts/icons from a verified local manifest.

Run with the full UnrealEditor.exe and -ExecutePythonScript, for example:
  UnrealEditor.exe RMSim.uproject /Engine/Maps/Entry
    -ExecutePythonScript=tools/unreal/import_wt_map_ui.py
    -RenderOffScreen -d3d11 -MapPackage=... -WTUiReport=...
Use absolute project/script paths when launching outside the repository.

FontFileImportFactory requires an initialized Slate application. A Python
commandlet with -run=pythonscript -nullrhi triggers CurrentApplication.IsValid
in this engine; the verified import path is the full Editor shown above.
The calling driver owns editor shutdown after the requested imports complete.

Reads the prepared map package; writes only its UI assets and the requested report.
Original War Thunder files, map geometry, calibration and overview.json remain read only.
"""
import datetime
import hashlib
import json
from pathlib import Path
import re
import traceback
import unreal


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def argument(name, default=None):
    match = re.search(r'(?<!\S)-' + name + r'=(?:"([^"]+)"|(\S+))', unreal.SystemLibrary.get_command_line())
    return (match.group(1) or match.group(2)) if match else default


def finish_asset_compilation(report, phase):
    # UE 5.8 AsyncCompilationHelpers.cpp registers this synchronous command;
    # AssetCompilingManager.cpp binds it to FinishAllCompilation(). Kismet's
    # console dispatcher calls IConsoleManager directly, without a player/world.
    entry = dict(phase=phase, command='Editor.AsyncAssetCompilationFinishAll',
                 started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 completed=False)
    report.setdefault('compilation_barriers', []).append(entry)
    unreal.SystemLibrary.execute_console_command(None, entry['command'])
    entry.update(completed=True, finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())


def texture_size(obj):
    return [int(obj.blueprint_get_size_x()), int(obj.blueprint_get_size_y())]


def main(report):
    package = Path(argument('MapPackage', str(Path(__file__).resolve().parents[2] / 'assets/maps/wt_vlaanderen'))).resolve(strict=True)
    overview_path = package / 'overview.json'
    overview_bytes = overview_path.read_bytes()
    overview = json.loads(overview_bytes.decode('utf-8-sig'))
    require(overview.get('map_id') == 'wt_vlaanderen', 'unexpected map identity')
    manifest_path = (package / overview['ui_assets_file']).resolve(strict=True)
    require(manifest_path.is_relative_to(package), 'UI manifest escapes the package')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    require(manifest.get('schema') == 'rmsim_wt_map_ui_assets_v1', 'unsupported UI manifest')
    require(manifest['map_id'] == overview['map_id'] and manifest['source_sha256'] == overview['source_sha256'], 'UI source identity differs')
    folder = '/Game/RMSim/Maps/wt_vlaanderen/UI'
    required_roles = {'font_asset', 'zone_font_asset', 'player_arrow_asset', 'hull_arrow_asset', 'capture_A', 'capture_B', 'capture_C', 'spawn', 'ping', 'poi', 'full_map_background_asset'}
    require({a['role'] for a in manifest['assets']} == required_roles and len(manifest['assets']) == len(required_roles), 'missing or duplicate UI roles')
    tasks = []
    for spec in manifest['assets']:
        source = (package / spec['file']).resolve(strict=True)
        require(source.is_relative_to(package / 'overview/ui'), 'UI source escapes its folder')
        require(sha(source) == spec['sha256'], 'UI source hash differs: ' + spec['file'])
        name = spec['asset_name']
        require(re.fullmatch(r'WT_Map[A-Za-z0-9_]+', name), 'invalid UI asset name')
        require(spec['kind'] in ('font', 'texture'), 'unsupported UI asset kind')
        expected_name = name + '_Font' if spec['kind'] == 'font' else name
        require(spec['asset'] == folder + '/' + expected_name + '.' + expected_name, 'UI asset path differs')
        field_asset = overview.get(spec['role']) if spec['role'].endswith('_asset') else overview.get('marker_assets', {}).get(spec['role'])
        require(field_asset == spec['asset'], 'overview UI asset reference differs for ' + spec['role'])
        task = unreal.AssetImportTask()
        for key, value in dict(filename=str(source), destination_path=folder, destination_name=name, automated=True, replace_existing=True, save=False).items():
            task.set_editor_property(key, value)
        if spec['kind'] == 'font':
            factory = unreal.FontFileImportFactory()
            policy = getattr(unreal.BatchCreateFontAsset, 'CREATE_IF_NO_FONT_EXISTS', None)
            require(policy is not None, 'UE does not expose automatic UFont creation')
            factory.set_editor_property('batch_create_font_asset', policy)
            task.set_editor_property('factory', factory)
        tasks.append(task)
    report.update(map_id=overview['map_id'], source_sha256=overview['source_sha256'], manifest_sha256=sha(manifest_path), overview_sha256_before=hashlib.sha256(overview_bytes).hexdigest(), script_sha256=sha(__file__))
    # Attach rows before any asset-level operation so a failure report includes
    # the exact role, expected dimensions, partial imports and last completed stage.
    imported = []
    report['assets'] = imported
    report['asset_count'] = 0
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(tasks)
    finish_asset_compilation(report, 'after_import')
    for spec, task in zip(manifest['assets'], tasks):
        row = dict(role=spec['role'], asset=spec['asset'], kind=spec['kind'],
                   source_file=spec['file'], source_sha256=spec['sha256'],
                   imported_object_paths=list(task.get_editor_property('imported_object_paths')),
                   status='pending', stage='loading')
        if spec['kind'] == 'texture':
            row['expected_pixel_size'] = spec['decoded_size']
        imported.append(row)
        report['current_role'] = spec['role']
        obj = unreal.load_asset(spec['asset'])
        require(obj is not None, 'expected UI asset was not created: ' + spec['asset'])
        row['asset'] = obj.get_path_name()
        if spec['kind'] == 'font':
            require(isinstance(obj, unreal.Font), 'expected a composite UFont: ' + spec['role'])
            face = unreal.load_asset(spec['face_asset'])
            require(isinstance(face, unreal.FontFace), 'expected original UFontFace: ' + spec['role'])
            row['stage'] = 'saving_font_face'
            require(unreal.EditorAssetLibrary.save_loaded_asset(face, False), 'failed to save UFontFace: ' + spec['role'])
            row['face_asset'] = face.get_path_name()
            row['font_cache_type'] = str(obj.get_editor_property('font_cache_type'))
        else:
            require(isinstance(obj, unreal.Texture2D), 'expected a UI Texture2D: ' + spec['role'])
            row['pixel_size_after_import'] = texture_size(obj)
            row['stage'] = 'applying_texture_settings'
            settings = dict(srgb=True, compression_settings=unreal.TextureCompressionSettings.TC_EDITOR_ICON, compression_no_alpha=False, mip_gen_settings=unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS, lod_group=unreal.TextureGroup.TEXTUREGROUP_UI, never_stream=True, filter=unreal.TextureFilter.TF_BILINEAR, address_x=unreal.TextureAddress.TA_CLAMP, address_y=unreal.TextureAddress.TA_CLAMP)
            for key, value in settings.items():
                obj.set_editor_property(key, value)
            row['texture_settings'] = {key: str(obj.get_editor_property(key)) for key in settings}
            row['pixel_size_before_compile'] = texture_size(obj)
            row['stage'] = 'finishing_texture_compilation'
            # Property changes rebuild platform data asynchronously. Blueprint
            # sizes may otherwise describe the temporary compile placeholder.
            finish_asset_compilation(report, 'settings_' + spec['role'])
            row['pixel_size_after_compile'] = texture_size(obj)
        row['stage'] = 'saving_asset'
        require(unreal.EditorAssetLibrary.save_loaded_asset(obj, False), 'failed to save UI asset: ' + spec['role'])
        row['saved'] = True
        if spec['kind'] == 'texture':
            row['stage'] = 'verifying_saved_dimensions'
            row['pixel_size'] = texture_size(obj)
            require(row['pixel_size'] == row['expected_pixel_size'],
                    'imported icon dimensions differ: role=' + spec['role']
                    + ' asset=' + row['asset']
                    + ' actual=' + str(row['pixel_size'])
                    + ' expected=' + str(row['expected_pixel_size'])
                    + ' after_import=' + str(row['pixel_size_after_import'])
                    + ' before_compile=' + str(row['pixel_size_before_compile'])
                    + ' after_compile=' + str(row['pixel_size_after_compile']))
        row.update(status='passed', stage='complete')
        report['asset_count'] += 1
        unreal.log('WT_MAP_UI_ASSET_IMPORTED ' + row['role'] + ' ' + row['asset'])
    report.pop('current_role', None)
    require(overview_path.read_bytes() == overview_bytes, 'overview changed during UI import')
    report.update(status='passed', asset_count=len(imported), assets=imported, overview_sha256_after=sha(overview_path), overview_unchanged=True)
    unreal.log('WT_MAP_UI_IMPORT_COMPLETE ' + str(len(imported)))


if __name__ == '__main__':
    destination = Path(argument('WTUiReport', str(Path(__file__).resolve().parents[2] / 'artifacts/maps/wt-zeppelin-minimap-20261119/ui-import-report.json')))
    report = dict(status='failed', started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    error = None
    try:
        main(report)
    except Exception as exc:
        error = exc
        report['failure'] = str(exc)
        for row in report.get('assets', []):
            if row.get('status') == 'pending':
                row['status'] = 'failed'
        report['traceback'] = traceback.format_exc()
        unreal.log_error('WT_MAP_UI_IMPORT_FAILED ' + str(exc))
    finally:
        report['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        unreal.log('WT_MAP_UI_IMPORT_REPORT ' + str(destination))
    if error:
        raise error
