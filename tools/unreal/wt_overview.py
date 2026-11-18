"""Import the original, calibrated overview image as an independent UE UI asset.

import_overview(package, folder) -> native settings/readback report.
No World, source geometry, materials or scene manifest are modified.
validate_overview() is pure Python so package identity can be checked before UE.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys
import traceback
import zlib


def _require(value, reason):
    if not value:
        raise ValueError(reason)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _local(package, relative):
    relative = Path(relative)
    _require(not relative.is_absolute() and '..' not in relative.parts, 'overview path must remain within package')
    path = (package / relative).resolve(strict=True)
    _require(path.is_relative_to(package) and path.is_file(), 'missing or escaping overview file')
    return path


def validate_overview(package, folder=None):
    package = Path(package).resolve(strict=True)
    path = package / 'overview.json'
    definition = _json(path)
    source = _json(package / 'scene-source.json')
    descriptor = _json(package / 'map.json')
    _require(definition.get('schema') == 'rmsim_map_overview_v1', 'unsupported overview schema')
    _require(definition['map_id'] == source['id'] == descriptor['id'], 'overview map identity differs')
    _require(definition['source_sha256'] == source['source_sha256'] == descriptor['source_sha256'], 'overview source identity differs')
    folder = (folder or f"/Game/RMSim/Maps/{source['id']}").rstrip('/')
    asset_path = f'{folder}/UI/WT_MapOverview.WT_MapOverview'
    _require(definition['texture_asset'] == asset_path, 'overview Texture2D path differs from the import folder')
    image = _local(package, definition['image_file'])
    calibration_file = _local(package, definition['calibration_file'])
    _require(_sha(image) == definition['image_sha256'], 'overview image SHA256 differs')
    _require(_sha(calibration_file) == definition['calibration_sha256'], 'overview calibration SHA256 differs')
    calibration = _json(calibration_file)
    _require(calibration.get('schema') == 'wt_map_georeference_v1' and calibration.get('status') == 'passed',
             'overview calibration has not passed')
    _require(calibration['map_id'] == definition['map_id'] and calibration['source_sha256'] == definition['source_sha256'],
             'calibration identity differs from the overview')
    _require(calibration['image_sha256'] == definition['image_sha256'], 'calibration refers to another image')
    _require(calibration['image_ros_corners_m'] == definition['image_ros_corners_m'], 'overview corners differ from the verified calibration')
    _require(calibration['map_descriptor']['sha256'] == _sha(package / 'map.json'), 'map descriptor changed since overview calibration')
    _require(source['source_to_ros_matrix'] == calibration['source_to_ros_matrix'] and
             source['source_translation_m'] == calibration['source_translation_m'], 'overview coordinate basis changed')
    corners = definition['image_ros_corners_m']
    _require(set(corners) == {'top_left', 'top_right', 'bottom_left'}, 'overview needs exactly three calibrated corners')
    for values in corners.values():
        _require(isinstance(values, list) and len(values) == 2 and all(isinstance(x, (int, float)) and math.isfinite(x) for x in values),
                 'nonfinite or invalid overview corner')
    u = [corners['top_right'][i] - corners['top_left'][i] for i in range(2)]
    v = [corners['bottom_left'][i] - corners['top_left'][i] for i in range(2)]
    _require(abs(u[0] * v[1] - u[1] * v[0]) > 1.e-6, 'singular overview calibration')
    _require(16 <= definition['minimap_span_m'] <= 32768, 'invalid near-map span')
    png = image.read_bytes()
    _require(len(png) >= 33 and png[:8] == b'\x89PNG\r\n\x1a\n' and png[8:16] == b'\0\0\0\rIHDR', 'overview is not a valid PNG header')
    _require(zlib.crc32(png[12:29]) & 0xffffffff == struct.unpack('>I', png[29:33])[0], 'overview PNG header CRC differs')
    dimensions = list(struct.unpack('>II', png[16:24]))
    _require(dimensions == [definition['image_width'], definition['image_height']] ==
             [calibration['image_width'], calibration['image_height']] == [1024, 1024], 'overview image dimensions differ')
    _require(png[24] == 8 and png[25] in (2, 6), 'overview source must be 8-bit RGB/RGBA')
    return dict(package=package, definition=definition, definition_path=path, image=image, calibration_path=calibration_file,
                folder=folder, expected_asset=asset_path, image_dimensions=dimensions, definition_sha256=_sha(path))


def import_overview(package, folder):
    checked = validate_overview(package, folder)
    import unreal
    from import_vehicle_assets import import_asset
    definition = checked['definition']
    imported = import_asset(checked['image'], f"{checked['folder']}/UI", 'WT_MapOverview')
    textures = [obj for obj in imported if isinstance(obj, unreal.Texture2D)]
    _require(len(textures) == 1, 'overview import did not return exactly one Texture2D')
    texture = textures[0]
    _require(texture.get_path_name() == checked['expected_asset'], 'overview imported under an unexpected asset name')
    settings = {
        'lod_group': unreal.TextureGroup.TEXTUREGROUP_UI,
        'compression_settings': unreal.TextureCompressionSettings.TC_EDITOR_ICON,
        'mip_gen_settings': unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS,
        'never_stream': True,
        'srgb': True,
        'virtual_texture_streaming': False,
        'address_x': unreal.TextureAddress.TA_CLAMP,
        'address_y': unreal.TextureAddress.TA_CLAMP,
        'filter': unreal.TextureFilter.TF_BILINEAR,
        'lod_bias': 0,
    }
    for key, value in settings.items():
        texture.set_editor_property(key, value)
    tags = {
        'RMSimOverviewSchema': definition['schema'],
        'RMSimOverviewSourceSHA256': definition['source_sha256'],
        'RMSimOverviewImageSHA256': definition['image_sha256'],
        'RMSimOverviewCalibrationSHA256': definition['calibration_sha256'],
        'RMSimOverviewDefinitionSHA256': checked['definition_sha256'],
    }
    for key, value in tags.items():
        unreal.EditorAssetLibrary.set_metadata_tag(texture, key, value)
    _require(unreal.EditorAssetLibrary.save_loaded_asset(texture, False), 'overview Texture2D save failed')
    saved = unreal.load_asset(checked['expected_asset'])
    _require(isinstance(saved, unreal.Texture2D), 'saved overview Texture2D is unavailable')
    readback = {}
    for key, expected in settings.items():
        actual = saved.get_editor_property(key)
        _require(actual == expected, 'saved overview property differs: ' + key)
        readback[key] = actual if isinstance(actual, (str, bool, int, float)) else str(actual)
    for key, expected in tags.items():
        _require(unreal.EditorAssetLibrary.get_metadata_tag(saved, key) == expected, 'saved overview source tag differs: ' + key)
    dimensions = [int(saved.blueprint_get_size_x()), int(saved.blueprint_get_size_y())]
    _require(dimensions == checked['image_dimensions'], 'native overview texture dimensions differ')
    content = Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir()))
    uasset = content / (checked['expected_asset'].split('.')[0].removeprefix('/Game/') + '.uasset')
    _require(uasset.is_file(), 'saved overview .uasset does not exist')
    report = dict(schema='wt_overview_native_import_v1', status='passed', map_id=definition['map_id'],
        source_sha256=definition['source_sha256'], texture_asset=saved.get_path_name(),
        overview_definition_sha256=checked['definition_sha256'], image_sha256=definition['image_sha256'],
        calibration_sha256=definition['calibration_sha256'], image_ros_corners_m=definition['image_ros_corners_m'],
        minimap_span_m=definition['minimap_span_m'], native_dimensions=dimensions, native_settings=readback,
        native_metadata=tags, uasset={'file': str(uasset), 'bytes': uasset.stat().st_size, 'sha256': _sha(uasset)},
        module_sha256=_sha(__file__))
    unreal.log('MAP_OVERVIEW_IMPORTED ' + json.dumps(report, sort_keys=True))
    return report


def _argument(command, key):
    match = re.search(r'-' + re.escape(key) + r'=(?:"([^"]+)"|(\S+))', command)
    return next((value for value in match.groups() if value is not None), None) if match else None


def main():
    import unreal
    command = unreal.SystemLibrary.get_command_line()
    package = _argument(command, 'OverviewPackage')
    output_value = _argument(command, 'OverviewReport')
    _require(package and output_value, 'pass -OverviewPackage=<package> -OverviewReport=<fresh report.json>')
    output = Path(output_value).resolve()
    _require(not output.exists(), 'overview import report must be fresh')
    report = {'status': 'failed', 'command': command, 'script_sha256': _sha(__file__)}
    failure = None
    try:
        source = _json(Path(package) / 'map.json')
        folder = _argument(command, 'OverviewFolder') or f"/Game/RMSim/Maps/{source['id']}"
        report.update(import_overview(package, folder))
    except Exception as error:
        failure = error
        report['failure'] = str(error)
        report['traceback'] = traceback.format_exc()
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if failure is not None:
        raise RuntimeError('Overview native import failed; see ' + str(output)) from failure


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
