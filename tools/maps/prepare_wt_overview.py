"""Rebuild a source-calibrated Flanders HUD map from the original DXP/VROMFS.

Only overview.json and overview/ files are written in the prepared map package.
The scene manifest, geometry, saved World and original game files are read only.
"""
import argparse
import contextlib
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import traceback

sys.dont_write_bytecode = True

from dagor_codec import configure_oodle
from export_wt_textures import decode_texture
from wt_blk import decode_blk, shared_context
from wt_vromfs import read_vromfs


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_atomic(path, data):
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    with temporary.open('xb') as stream:
        stream.write(data)
    temporary.replace(path)
    return True


def prepare(args, report):
    package = args.package.resolve(strict=True)
    proof_path = args.calibration.resolve(strict=True)
    proof_bytes = proof_path.read_bytes()
    require(sha_bytes(proof_bytes) == args.calibration_sha256.lower(), 'calibration proof SHA256 differs')
    proof = json.loads(proof_bytes.decode('utf-8-sig'))
    require(proof.get('schema') == 'wt_map_georeference_v1' and proof.get('status') == 'passed', 'calibration proof is not passed')
    descriptor = read(package / 'map.json')
    source = read(package / 'scene-source.json')
    require(proof['map_id'] == descriptor['id'] == source['id'] == 'wt_vlaanderen', 'map identity differs')
    require(proof['source_sha256'] == descriptor['source_sha256'] == source['source_sha256'], 'scene source identity differs')
    require(sha(package / 'map.json') == proof['map_descriptor']['sha256'], 'map descriptor changed since calibration')
    require(source['source_to_ros_matrix'] == proof['source_to_ros_matrix'] and
            source['source_translation_m'] == proof['source_translation_m'], 'scene coordinate transform changed')
    orientation = proof['orientation_test']
    require(orientation['all_three_ponds_match'] and orientation['threshold_sweep_same_winner'] and
            proof['landmark_count'] >= 4, 'calibration lacks independent geometry/image agreement')

    level_proof = proof['level_coordinates']
    files, archive = read_vromfs(level_proof['archive']['path'])
    require(archive['sha256'] == level_proof['archive']['sha256'], 'original level archive changed')
    level_name = level_proof['entry']['name']
    raw_level = files[level_name]
    require(sha_bytes(raw_level) == level_proof['entry']['source_sha256'], 'original level BLK changed')
    names, dictionary, context = shared_context(files)
    root, decoding = decode_blk(raw_level, names, dictionary)
    root_params = {p['name']: p for p in root['params']}
    coordinates = {}
    for name in ('tankMapCoord0', 'tankMapCoord1'):
        require(root_params[name]['type'] == 'point2', 'map coordinate is not the original point2 type')
        coordinates[name] = root_params[name]['value']
        require(coordinates[name] == level_proof['independently_read_raw_parameters']['fields'][name]['value'],
                'original map coordinate differs from geometric calibration')
    del files

    image_entry = proof['image_source_entry']
    pack_record = proof['image_source_pack']
    pack_path = Path(image_entry['source']).resolve(strict=True)
    require(sha(pack_path) == pack_record['sha256'], 'original DXP texture pack changed')
    require(image_entry['name'] == 'avg_vlaanderen_tankmap', 'unexpected overview texture name')
    require(args.dae_root.is_dir(), 'pass the original dagor_asset_explorer src/dae directory')
    configure_oodle(str(args.oodle_library.resolve(strict=True)))
    sys.path.insert(0, str(args.dae_root.resolve(strict=True)))
    with (args.output / 'decoder.log').open('w', encoding='utf-8') as logfile, contextlib.redirect_stdout(logfile):
        from parse.material import DDSxTexturePack2
        import util.log as log
        log.log = lambda *a, **k: None
        texture_pack = DDSxTexturePack2(str(pack_path))
        matching = [tex for tex in texture_pack.getPackedFiles() if tex.name == image_entry['name']]
        require(len(matching) == 1, 'source DXP texture name is not unique')
        texture = matching[0]
        require(texture.offset == image_entry['offset'] and texture.size == image_entry['packed_bytes'],
                'source DXP texture offset/packed byte count changed')
        image, floats = decode_texture(texture)
        require(image is not None and floats is None and list(image.size) == [1024, 1024], 'tank map is not a 1024px image')
        png = io.BytesIO()
        image.save(png, format='PNG')
        image_bytes = png.getvalue()
    require(sha_bytes(image_bytes) == proof['image_sha256'] == image_entry['sha256'],
            'fresh source texture decode differs from the calibrated PNG bytes')

    folder = args.ue_folder.rstrip('/') if args.ue_folder else f"/Game/RMSim/Maps/{source['id']}"
    require(folder.startswith('/Game/') and '..' not in folder, 'invalid UE map folder')
    corners = {name: [0.0 if value == 0 else value for value in values]
               for name, values in proof['image_ros_corners_m'].items()}
    definition = dict(schema='rmsim_map_overview_v1', map_id=source['id'], display_name=descriptor['display_name'],
        source_sha256=source['source_sha256'], texture_asset=f'{folder}/UI/WT_MapOverview.WT_MapOverview',
        image_file='overview/avg_vlaanderen_tankmap.png', image_sha256=proof['image_sha256'],
        image_width=1024, image_height=1024, image_ros_corners_m=corners,
        corner_semantics=proof['corner_semantics'], minimap_span_m=512,
        calibration_file='overview/calibration.json', calibration_sha256=sha_bytes(proof_bytes),
        provenance=dict(texture_pack=str(pack_path), texture_pack_sha256=pack_record['sha256'],
            texture_name=image_entry['name'], texture_offset=image_entry['offset'], texture_packed_bytes=image_entry['packed_bytes'],
            level_archive=archive['path'], level_archive_sha256=archive['sha256'], level_entry=level_name,
            level_entry_sha256=sha_bytes(raw_level), original_level_coordinates=coordinates,
            orientation=orientation['selected'], landmark_count=proof['landmark_count']))
    encoded = (json.dumps(definition, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    outputs = [(package / definition['image_file'], image_bytes),
               (package / definition['calibration_file'], proof_bytes), (package / 'overview.json', encoded)]
    report['outputs'] = [dict(path=str(path), sha256=sha_bytes(data), bytes=len(data), changed=write_atomic(path, data))
                         for path, data in outputs]
    require(read(package / 'overview.json') == definition, 'overview definition readback differs')
    require(all(sha(path) == sha_bytes(data) for path, data in outputs), 'prepared overview file readback differs')
    report.update(status='passed', source_sha256=source['source_sha256'], image_sha256=proof['image_sha256'],
        calibration_sha256=sha_bytes(proof_bytes), image_ros_corners_m=corners, texture_asset=definition['texture_asset'],
        original_level_archive=archive, original_level_decoding=decoding, original_shared_context=context,
        fresh_dxp_decode_matches_calibrated_png=True, original_map_coordinates=coordinates,
        spawn=proof['spawn'], scripts={p.name: sha(p) for p in [Path(__file__), Path(__file__).with_name('wt_blk.py'),
            Path(__file__).with_name('wt_vromfs.py'), Path(__file__).with_name('export_wt_textures.py')]})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--calibration', type=Path, required=True)
    parser.add_argument('--calibration-sha256', required=True)
    parser.add_argument('--dae-root', type=Path, required=True)
    parser.add_argument('--oodle-library', type=Path, required=True)
    parser.add_argument('--ue-folder')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), 'output directory must be fresh')
    args.output.mkdir(parents=True)
    report = dict(status='failed', command=sys.argv, started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    code = 0
    try:
        prepare(args, report)
    except Exception as error:
        report['failure'] = str(error)
        report['traceback'] = traceback.format_exc()
        code = 1
    finally:
        report['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        output = args.output / 'verification.json'
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'status': report['status'], 'verification': str(output.resolve()), 'sha256': sha(output),
            'texture_asset': report.get('texture_asset'), 'outputs': report.get('outputs'), 'failure': report.get('failure')}, ensure_ascii=True))
    raise SystemExit(code)
