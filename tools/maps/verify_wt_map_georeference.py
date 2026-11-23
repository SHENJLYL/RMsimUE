"""Bind the original Flanders tank-map image to source geometry without running UE.

Checks all eight axis-swap/reflection candidates against three original water
meshes and records separate, visually identified hippodrome landmarks. Input
archives and prepared source geometry are read only. NumPy and Pillow are needed.
"""
import argparse
from compression import zstd
import datetime
import hashlib
import itertools
import json
from pathlib import Path
import struct
import sys
import traceback

import numpy as np
from PIL import Image, ImageDraw

from wt_blk import decode_blk, shared_context
from wt_vromfs import read_vromfs


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def require(value, reason):
    if not value:
        raise ValueError(reason)


def file_record(path):
    path = Path(path).resolve(strict=True)
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha(path)}


def raw_point_parameters(raw, names, dictionary):
    """Read this level's raw parameter records separately from decode_blk()."""
    require(raw[0] == 5, 'this verification expects the observed dictionary-compressed level')
    data = zstd.decompress(raw[1:], zstd_dict=zstd.ZstdDict(dictionary))
    offset = 0

    def varuint():
        nonlocal offset
        result = 0
        for shift in range(0, 35, 7):
            require(offset < len(data), 'truncated independent varuint')
            byte = data[offset]
            offset += 1
            result |= (byte & 127) << shift
            if not byte & 128:
                require(result <= 0xffffffff, 'independent varuint overflow')
                return result
        raise ValueError('independent varuint too long')

    require(varuint() == 0, 'level unexpectedly has local names; review independent parser')
    blocks, params, complex_size = varuint(), varuint(), varuint()
    complex_offset = offset
    complex_bytes = data[offset:offset + complex_size]
    offset += complex_size
    require(offset + params * 8 <= len(data), 'truncated independent parameter table')
    result = {}
    keys = {'tankMapCoord0', 'tankMapCoord1', 'mapCoord0', 'mapCoord1'}
    for index in range(params):
        record = data[offset + index * 8:offset + (index + 1) * 8]
        name_id = int.from_bytes(record[:3], 'little')
        type_code = record[3]
        require(name_id < len(names), 'independent name ID out of range')
        name = names[name_id]
        if name not in keys:
            continue
        require(name not in result and type_code == 4, 'duplicate or non-point2 coordinate field')
        value_offset = int.from_bytes(record[4:], 'little')
        require(value_offset + 8 <= len(complex_bytes), 'coordinate offset out of range')
        value_bytes = complex_bytes[value_offset:value_offset + 8]
        result[name] = dict(parameter_index=index, shared_name_id=name_id, type_code=type_code,
            type_name='point2', record_hex=record.hex(), complex_offset=value_offset,
            complex_value_hex=value_bytes.hex(), value=list(struct.unpack('<2f', value_bytes)))
    require(set(result) == keys, 'coordinate fields not found in independent raw table')
    return {'decoded_bytes': len(data), 'blocks': blocks, 'parameters': params,
            'complex_bytes': complex_size, 'complex_stream_offset': complex_offset, 'fields': result}


def transform_uv(uv, candidate):
    result = uv[..., ::-1].copy() if candidate['transpose'] else uv.copy()
    if candidate['flip_u']:
        result[..., 0] = 1.0 - result[..., 0]
    if candidate['flip_v']:
        result[..., 1] = 1.0 - result[..., 1]
    return result


def inverse_uv(uv, candidate):
    result = np.asarray(uv, dtype=float).copy()
    if candidate['flip_u']:
        result[..., 0] = 1.0 - result[..., 0]
    if candidate['flip_v']:
        result[..., 1] = 1.0 - result[..., 1]
    return result[..., ::-1].copy() if candidate['transpose'] else result


def rasterize(triangles_uv, dimensions):
    canvas = Image.new('L', dimensions)
    drawer = ImageDraw.Draw(canvas)
    for triangle in triangles_uv * np.asarray(dimensions):
        drawer.polygon([tuple(p) for p in triangle], fill=1)
    return np.asarray(canvas, dtype=np.uint8) != 0


def main(args, report):
    package = args.package.resolve(strict=True)
    overview = args.overview_source.resolve(strict=True)
    level_dir = args.level_evidence.resolve(strict=True)
    manifest_path = package / 'scene-source.json'
    manifest_bytes = manifest_path.read_bytes()
    source = json.loads(manifest_bytes.decode('utf-8-sig'))
    descriptor_path = package / 'map.json'
    descriptor = read(descriptor_path)
    require(source['id'] == descriptor['id'] == 'wt_vlaanderen', 'unexpected scene identity')
    require(source['source_sha256'] == descriptor['source_sha256'], 'map/source identities differ')
    original = Path(source['extraction_root']).resolve(strict=True)
    static_root = original / 'static-02'
    static_path = static_root / 'scene.json'
    static = read(static_path)
    require(static['sha256'] == source['source_sha256'], 'original water geometry belongs to another source')
    report.update(schema='wt_map_georeference_v1', map_id=source['id'], source_sha256=source['source_sha256'],
        map_descriptor=file_record(descriptor_path), source_manifest_snapshot={
            'path': str(manifest_path), 'bytes': len(manifest_bytes),
            'sha256': hashlib.sha256(manifest_bytes).hexdigest()},
        original_static_scene=file_record(static_path), source_to_ros_matrix=source['source_to_ros_matrix'],
        source_translation_m=source['source_translation_m'])
    require(np.array_equal(source['source_to_ros_matrix'], [[1, 0, 0], [0, 0, -1], [0, 1, 0]]),
        'source-to-ROS basis changed')
    require(np.array_equal(source['source_translation_m'], [0, 0, 0]), 'source origin moved')

    image_inventory_path = overview / 'source-inventory.json'
    image_inventory = read(image_inventory_path)
    image_entry = next(x for x in image_inventory['textures'] if x['name'] == 'avg_vlaanderen_tankmap')
    image_path = Path(image_entry['file']).resolve(strict=True)
    require(sha(image_path) == image_entry['sha256'], 'source-map image hash changed')
    pack_entry = next(x for x in image_inventory['packs'] if x['path'] == image_entry['source'])
    require(sha(pack_entry['path']) == pack_entry['sha256'], 'source image pack changed')
    image = Image.open(image_path).convert('RGB')
    require(list(image.size) == image_entry['size'] == [1024, 1024], 'unexpected map image dimensions')
    pixels = np.asarray(image).astype(np.int16)
    report.update(image_sha256=sha(image_path), image_width=image.width, image_height=image.height,
        image=file_record(image_path), image_source_inventory=file_record(image_inventory_path),
        image_source_pack=pack_entry, image_source_entry=image_entry)

    decoding_path = level_dir / 'decoded/verification.json'
    decoding = read(decoding_path)
    level_name = 'levels/avg_vlaanderen.blk'
    level_entry = next(x for x in decoding['decoded'] if x['name'] == level_name)
    files, archive = read_vromfs(decoding['archive']['path'])
    require(archive['sha256'] == decoding['archive']['sha256'], 'level archive changed')
    raw_level = files[level_name]
    require(hashlib.sha256(raw_level).hexdigest() == level_entry['source_sha256'], 'level BLK bytes changed')
    names, dictionary, context = shared_context(files)
    root, decoded_info = decode_blk(raw_level, names, dictionary)
    require(root == read(level_entry['output']), 'fresh level decode differs from saved evidence')
    require(sha(level_entry['output']) == level_entry['output_sha256'], 'saved level JSON hash changed')
    raw_points = raw_point_parameters(raw_level, names, dictionary)
    level_params = {p['name']: p for p in root['params']}
    for key, item in raw_points['fields'].items():
        require(level_params[key]['type'] == 'point2' and level_params[key]['value'] == item['value'],
            'independent raw point2 disagrees: ' + key)
    coord0 = np.array(raw_points['fields']['tankMapCoord0']['value'])
    coord1 = np.array(raw_points['fields']['tankMapCoord1']['value'])
    require(np.all(coord1 > coord0), 'invalid tank-map range')
    report['level_coordinates'] = dict(archive=archive, entry=level_entry, shared_context=context,
        shared_namemap_sha256=hashlib.sha256(files['\xff?nm']).hexdigest(),
        dictionary_sha256=hashlib.sha256(dictionary).hexdigest(), fresh_decode=decoded_info,
        independently_read_raw_parameters=raw_points, decoding_report=file_record(decoding_path))
    del files

    water_index = next(i for i, x in enumerate(static['materials']) if x['shader'] == 'water_aces')
    water = []
    for item in static['objects']:
        path = static_root / item['file']
        with np.load(path, allow_pickle=False) as data:
            keep = data['material_ids'] == water_index
            if not keep.any():
                continue
            xyz = data['vertices'][data['triangles'][keep]].astype(np.float64)
        xz = xyz[..., [0, 2]]
        require(np.all(np.isfinite(xz)), 'nonfinite original water geometry')
        uv = (xz - coord0) / (coord1 - coord0)
        require(np.all((uv >= 0) & (uv <= 1)), 'source water leaves the calibrated map range')
        cross = (xz[:, 1, 0] - xz[:, 0, 0]) * (xz[:, 2, 1] - xz[:, 0, 1]) - \
            (xz[:, 1, 1] - xz[:, 0, 1]) * (xz[:, 2, 0] - xz[:, 0, 0])
        areas = abs(cross) * 0.5
        require(float(areas.sum()) > 0, 'water surface has no projected area')
        centroid = np.average(xz.mean(axis=1), axis=0, weights=areas)
        water.append(dict(name=item['name'], source=file_record(path), triangles=int(len(xyz)), uv=uv,
            bounds_dagor_xz_m={'lo': xz.reshape(-1, 2).min(axis=0).tolist(),
                              'hi': xz.reshape(-1, 2).max(axis=0).tolist()},
            projected_area_m2=float(areas.sum()), centroid_dagor_xz_m=centroid.tolist()))
    require(len(water) == 3, 'expected the three independently extracted source water surfaces')
    thresholds = [{'blue_minus_red_min': 12, 'blue_minus_green_min': 6, 'blue_min': 35},
                  {'blue_minus_red_min': 8, 'blue_minus_green_min': 3, 'blue_min': 30},
                  {'blue_minus_red_min': 16, 'blue_minus_green_min': 9, 'blue_min': 40}]
    blue_masks = [(pixels[:, :, 2] - pixels[:, :, 0] >= t['blue_minus_red_min']) &
                  (pixels[:, :, 2] - pixels[:, :, 1] >= t['blue_minus_green_min']) &
                  (pixels[:, :, 2] >= t['blue_min']) for t in thresholds]
    scores = []
    all_masks = {}
    for swap, flip_u, flip_v in itertools.product([False, True], repeat=3):
        candidate = dict(transpose=swap, flip_u=flip_u, flip_v=flip_v)
        masks = [rasterize(transform_uv(w['uv'], candidate), image.size) for w in water]
        combined = np.logical_or.reduce(masks)
        per_water = []
        for w, mask in zip(water, masks):
            hits = mask & blue_masks[0]
            ys, xs = np.nonzero(hits)
            per_water.append(dict(source_file=Path(w['source']['path']).name, projected_pixels=int(mask.sum()),
                visible_blue_pixels=int(hits.sum()), visible_blue_fraction=float(hits.sum() / mask.sum()),
                observed_blue_centroid_px=[float(xs.mean()), float(ys.mean())] if len(xs) else None))
        candidate.update(projected_pixels=int(combined.sum()), water=per_water,
            threshold_blue_fractions=[float((combined & blue).sum() / combined.sum()) for blue in blue_masks])
        scores.append(candidate)
        all_masks[(swap, flip_u, flip_v)] = masks
    best = max(scores, key=lambda x: x['threshold_blue_fractions'][0])
    best_key = tuple(best[k] for k in ('transpose', 'flip_u', 'flip_v'))
    runner_up = sorted((x['threshold_blue_fractions'][0] for x in scores), reverse=True)[1]
    require(best['threshold_blue_fractions'][0] > runner_up + 0.2, 'map orientation is ambiguous')
    require(all(x['visible_blue_pixels'] >= 100 and x['visible_blue_fraction'] >= 0.15 for x in best['water']),
        'winning orientation must independently match all three ponds')
    require(all(max(scores, key=lambda x: x['threshold_blue_fractions'][i]) is best for i in range(len(thresholds))),
        'orientation changes under the independent colour-threshold sweep')
    report['orientation_test'] = dict(scope='projected source-water triangles against visible blue map pixels',
        image_water_thresholds=thresholds, candidates=scores, selected={k: best[k] for k in ('transpose', 'flip_u', 'flip_v')},
        primary_winner_fraction=best['threshold_blue_fractions'][0], primary_runner_up_fraction=runner_up,
        all_three_ponds_match=True, threshold_sweep_same_winner=True,
        interpretation='water polygons include shoreline; colour fraction establishes image orientation, not a shoreline geometry error')

    def source_to_image(xz):
        return transform_uv((np.asarray(xz) - coord0) / (coord1 - coord0), best) * np.array(image.size)

    corners = {}
    for name, uv in [('top_left', [0., 0.]), ('top_right', [1., 0.]), ('bottom_left', [0., 1.])]:
        xz = coord0 + inverse_uv(uv, best) * (coord1 - coord0)
        corners[name] = [float(xz[0]), float(-xz[1])]
    report['image_ros_corners_m'] = corners
    report['corner_semantics'] = 'continuous texture edges UV(0,0), UV(1,0), UV(0,1); not texel centers'
    report['metres_per_pixel'] = ((coord1 - coord0) / np.array(image.size)).tolist()
    water_records = []
    for w, measured in zip(water, best['water']):
        projected = source_to_image(np.array(w['uv']) * (coord1 - coord0) + coord0)
        value = {k: v for k, v in w.items() if k != 'uv'}
        value.update(projected_centroid_px=source_to_image(w['centroid_dagor_xz_m']).tolist(),
            projected_bounds_px={'lo': projected.reshape(-1, 2).min(axis=0).tolist(),
                                 'hi': projected.reshape(-1, 2).max(axis=0).tolist()}, image_measurement=measured)
        water_records.append(value)
    report['water_landmarks'] = water_records

    layout_path = original / 'layout-06/layer-0-instances.json'
    layout = read(layout_path)
    landmark_specs = [('hippodrome_tribune_a', [737., 437., 848., 480.], 'grandstands along the upper side of the oval racetrack'),
                      ('hippodrome_stables_a', [711., 490., 783., 532.], 'stables inside the left half of the oval racetrack')]
    landmarks = []
    for name, roi, description in landmark_specs:
        entry = layout[name]
        require(not entry['pos_inst'], 'landmark is expected to contain a full original affine transform')
        mesh = next(x for x in source['meshes'] if x.get('source') == name)
        rmi_path = package / mesh['instances_file']
        raw_rmi = rmi_path.read_bytes()
        require(raw_rmi[:4] == b'RMI1', 'invalid prepared instance signature')
        count, = struct.unpack_from('<I', raw_rmi, 4)
        rows = np.frombuffer(raw_rmi[8:], dtype='<f4').reshape(-1, 10)
        require(count == len(rows) == len(entry['instances']), 'landmark instance count differs')
        positions = []
        for values, prepared in zip(entry['instances'], rows):
            matrix = np.asarray(values).reshape(3, 4)
            position = matrix[:, 3]
            ros = np.array([position[0], -position[2], position[1]])
            require(np.max(abs(prepared[:3] - ros)) < 0.0002, 'landmark prepared placement differs from original')
            px = source_to_image(position[[0, 2]])
            inside = bool(roi[0] <= px[0] <= roi[2] and roi[1] <= px[1] <= roi[3])
            require(inside, 'source landmark does not project to independently observed image feature')
            positions.append(dict(original_position_dagor_m=position.tolist(), position_ros_m=ros.tolist(),
                image_pixel=px.tolist(), prepared_rmi_position_ros_m=prepared[:3].tolist(), inside_observed_roi=inside))
        landmarks.append(dict(source_model=name, source_layout=file_record(layout_path), prepared_instances=file_record(rmi_path),
            image_observation=dict(method='visual inspection of the unmodified original 1024px map',
                feature=description, roi_pixels=roi), positions=positions))
    report['building_landmarks'] = landmarks
    report['landmark_count'] = len(water_records) + len(landmarks)
    spawn = next(x for x in descriptor['spawns'] if x['id'] == 'default')
    spawn_ros = np.array(spawn['position_m'])
    spawn_px = source_to_image([spawn_ros[0], -spawn_ros[1]])
    report['spawn'] = dict(id=spawn['id'], position_ros_m=spawn_ros.tolist(), yaw_rad=spawn['yaw_rad'],
        image_uv=(spawn_px / np.array(image.size)).tolist(), image_pixel=spawn_px.tolist())
    origin_px = source_to_image([spawn_ros[0], -spawn_ros[1]])
    report['image_axis_directions_per_ros_m'] = {
        'positive_x_px': (source_to_image([spawn_ros[0] + 1, -spawn_ros[1]]) - origin_px).tolist(),
        'positive_y_px': (source_to_image([spawn_ros[0], -spawn_ros[1] - 1]) - origin_px).tolist()}
    report['decoder_review'] = dict(scope='observed label-5 level; key coordinate parameter records independently decoded',
        reviewed_rules=['24-bit parameter name ID plus 8-bit type in little-endian record',
                        'point2 uses two little-endian float32 values at the complex-data byte offset',
                        'shared namemap is reused; this level has zero local names',
                        'block names use name ID + 1; parameter names use zero-based IDs',
                        'consecutive child ranges and exact stream coverage are checked',
                        'colour parameters remain the original four little-endian bytes'],
        references=[file_record(overview / n) for n in ['blk_serialize-reference.cpp', 'blk_shared-reference.h',
                                                       'blk_zstd-reference.cpp', 'dag_vromfs-reference.h',
                                                       'vromfsLoad-reference.cpp', 'dag_zstdObfuscate-reference.h']],
        implementation=[file_record(Path(__file__).with_name(n)) for n in ['wt_blk.py', 'wt_vromfs.py']])
    report['status'] = 'passed'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--overview-source', type=Path, required=True)
    parser.add_argument('--level-evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), 'output directory must be fresh')
    args.output.mkdir(parents=True)
    report = dict(status='failed', started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        script=file_record(__file__), command=sys.argv, python=sys.version, numpy=np.__version__)
    code = 0
    try:
        main(args, report)
    except Exception as error:
        report['failure'] = str(error)
        report['traceback'] = traceback.format_exc()
        code = 1
    finally:
        report['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        out = args.output / 'verification.json'
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'status': report['status'], 'verification': str(out.resolve()),
            'sha256': sha(out), 'corners': report.get('image_ros_corners_m'), 'spawn': report.get('spawn'),
            'landmarks': report.get('landmark_count'), 'failure': report.get('failure')}, ensure_ascii=True))
    raise SystemExit(code)
