"""Extract source-defined War Thunder foliage inputs for a streamed UE adapter.

Writes a separate supplemental package. Does not modify scene-source.json, spawn
instances, import UE assets, or substitute arbitrary foliage distributions.
"""
from __future__ import annotations
import argparse
import collections
import contextlib
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
import sys

import numpy as np
from PIL import Image


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def values(block: dict, name: str) -> list:
    return [item['value'] for item in block['params'] if item['name'] == name]


def one(block: dict, name: str, default=None):
    found = values(block, name)
    if len(found) > 1:
        raise ValueError('ambiguous singleton parameter: ' + name)
    return found[0] if found else default


def required_child(block: dict, name: str) -> dict:
    found = [item for item in block['children'] if item['name'] == name]
    if len(found) != 1:
        raise ValueError('expected one block: ' + name)
    return found[0]


def layer_ids(grass: dict, layer: dict) -> list[int]:
    result = set(values(layer, 'id'))
    channels = one(layer, 'maskChannel')
    if channels is not None:
        if len(channels) != 4 or sum(channels[:3]) <= 0:
            raise ValueError('invalid source maskChannel')
        for channel, name in enumerate(('redMaskBiomes', 'greenMaskBiomes', 'blueMaskBiomes')):
            if channels[channel] <= 0:
                continue
            blocks = [item for item in grass['children'] if item['name'] == name]
            if len(blocks) > 1:
                raise ValueError('duplicate source RGB biome block')
            result.update(values(blocks[0], 'id') if blocks else [channel])
    if not result or any(type(value) is not int or not 0 <= value < 64 for value in result):
        raise ValueError('source grass layer must declare exact biome IDs')
    return sorted(result)


def color_records(layer: dict) -> dict:
    result = {}
    for item in layer['params']:
        if item['type'] != 'color':
            continue
        raw = item['value']
        if len(raw) != 4 or any(type(v) is not int or not 0 <= v <= 255 for v in raw):
            raise ValueError('source E3DCOLOR must be four raw little-endian bytes')
        # DataBlock::findParam returns the first matching source parameter.
        result.setdefault(item['name'], dict(source_bgra=raw, rgba=[raw[2], raw[1], raw[0], raw[3]]))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--level-config', required=True, type=Path)
    parser.add_argument('--config-verification', required=True, type=Path)
    parser.add_argument('--landclass-json', required=True, type=Path)
    parser.add_argument('--texture-index', required=True, type=Path)
    parser.add_argument('--game-root', required=True, type=Path)
    parser.add_argument('--dae-root', required=True, type=Path)
    parser.add_argument('--oodle-library', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    readonly = [args.game_root.resolve(), args.dae_root.resolve(), args.texture_index.resolve().parent.parent]
    if any(out == path or out.is_relative_to(path) for path in readonly):
        raise ValueError('output must be outside original game, reader and extraction roots')
    if out.exists():
        raise ValueError('use a fresh output directory')
    source_level = args.game_root / 'levels/avg_vlaanderen.bin'
    source_level_hash = sha(source_level)
    if source_level_hash != '427c9b06b62af0635fa08f2d6238211b9850caf7717a0773f186e1835be63b8f':
        raise ValueError('source level differs from this audited Flanders dataset')
    config_hash = sha(args.level_config)
    verification = json.loads(args.config_verification.read_text(encoding='utf-8'))
    verified = [entry for entry in verification['decoded']
                if entry['name'] == 'levels/avg_vlaanderen.blk']
    if verification['status'] != 'passed' or len(verified) != 1 or verified[0]['output_sha256'] != config_hash:
        raise ValueError('level config must match the verified original archive entry')
    config = json.loads(args.level_config.read_text(encoding='utf-8'))
    grass = required_child(config, 'randomGrass')
    if one(grass, 'useGrassMaskBiomes') is not True:
        raise ValueError('this adapter requires explicit source biome-based grass')
    layers = [item for item in grass['children'] if item['name'] == 'layer']
    names = sorted({one(layer, 'res') for layer in layers})
    if any(not isinstance(name, str) or not re.fullmatch('[A-Za-z0-9_-]+', name) for name in names):
        raise ValueError('invalid source grass model name')
    landclass = json.loads(args.landclass_json.read_text(encoding='utf-8'))
    detail = next(item for item in landclass['root']['blocks'] if item['name'] == 'detail')
    detail_params = {item['name']: item['value'] for item in detail['params']}
    detail_textures = next(item for item in detail['blocks'] if item['name'] == 'landClassTextures')
    texture_params = {item['name']: item['value'] for item in detail_textures['params']}
    if detail_params['shader'] != 'landmesh_indexed_landclass' or detail_params['size'] != [2048.0, 2048.0]:
        raise ValueError('unsupported source core landclass dimensions or shader')
    out.mkdir(parents=True)
    for sub in ('models', 'textures', 'masks', 'source'):
        (out / sub).mkdir()
    shutil.copy2(args.level_config, out / 'source/level-config.json')
    shutil.copy2(args.landclass_json, out / 'source/landclass.json')
    from dagor_codec import configure_oodle
    from export_wt_textures import decode_texture
    configure_oodle(args.oodle_library)
    sys.path.insert(0, str(args.dae_root.resolve()))
    from parse.gameres import GameResourcePack
    from parse.realres import RendInst
    from parse.material import DDSxTexturePack2
    import util.log as log
    log.log = lambda *a, **k: None
    report = dict(schema='wt_foliage_source_v1', status='preparing_source',
                  source_level_sha256=source_level_hash,
                  official_commit='9639942de5a8e5b01d91977f60ed82cda22d5f41',
                  source_config=dict(file=str(args.level_config.resolve()), sha256=config_hash,
                    verification_file=str(args.config_verification.resolve()), verification_sha256=sha(args.config_verification),
                    archive=verification['archive'], entry=verified[0]),
                  source_landclass=dict(file=str(args.landclass_json.resolve()), sha256=sha(args.landclass_json),
                    resource=landclass['source']),
                  random_grass_source=grass, models=[], textures=[], layers=[])
    required_textures = {texture_params['indices'], texture_params['grass_weight']}
    try:
        with (out / 'decoder.log').open('w', encoding='utf-8') as debug, contextlib.redirect_stdout(debug):
            pack_path = args.game_root / 'content/base/res/vegetation.grp'
            pack = GameResourcePack(str(pack_path))
            source_entries = collections.defaultdict(list)
            for index in range(pack.getRealResEntryCnt()):
                entry = pack.getRealResEntry(index)
                if entry.getName() in names:
                    source_entries[entry.getName()].append((index, entry))
            pack_hash = sha(pack_path)
            for name in names:
                choices = source_entries[name]
                if len(choices) != 1 or choices[0][1]._RealResEntry__classId != 0xA7D3ED6A:
                    raise ValueError('expected one original RndGrass resource: ' + name)
                resource_id, entry = choices[0]
                source_resource = pack.getRealResource(resource_id)
                with pack_path.open('rb') as stream:
                    stream.seek(source_resource.offset)
                    raw = stream.read(source_resource.size)
                resource = RendInst(source_resource.filePath, name, source_resource.size, source_resource.offset)
                model = resource.getModel(0)
                vertices = np.asarray([model.getVertex(i) for i in range(model.vertCnt)], dtype='<f4')
                material_names = [material.getName() for material in model.materials]
                triangles, material_ids = [], []
                for obj in model:
                    material = None
                    source_id = None
                    for index, face in enumerate(obj.faces):
                        if index in obj.materials:
                            material = obj.materials[index]
                        if index in getattr(obj, 'source_material_ids', {}):
                            source_id = obj.source_material_ids[index]
                        triangles.append(face)
                        material_ids.append(source_id if source_id is not None else material_names.index(material))
                triangles = np.asarray(triangles, dtype='<i4')
                if not len(triangles) or not np.isfinite(vertices).all() or triangles.min() < 0 or triangles.max() >= len(vertices):
                    raise ValueError('invalid source grass geometry: ' + name)
                points = vertices[triangles].astype(np.float64)
                coincident = np.any(np.all(points == np.roll(points, 1, axis=1), axis=2), axis=1)
                if coincident.any():
                    raise ValueError('source grass requires separate coincident-face proof: ' + name)
                lo, hi = (np.asarray(v) for v in resource.sourceBounds)
                error = max(float((lo - vertices.min(axis=0)).max()), float((vertices.max(axis=0) - hi).max()), 0.0)
                if error > 0.05:
                    raise ValueError('source grass geometry exceeds declared bounds: ' + name)
                materials = [dict(name=m.getName(), shader=m.cls, parameters=m.par,
                    texture_slots=list(m.getTextureSlots())) for m in model.materials]
                for material in materials:
                    required_textures.update(t.split('*')[0].split('$')[0] for t in material['texture_slots'] if t)
                folder = out / 'models' / name
                folder.mkdir()
                mesh = folder / 'mesh.npz'
                np.savez_compressed(mesh, vertices=vertices, triangles=triangles,
                    uvs=np.asarray([model.getUV(i) for i in range(model.vertCnt)], dtype='<f4'),
                    normals=np.asarray(model.vertexNormals, dtype='<f4'),
                    material_ids=np.asarray(material_ids, dtype='<i4'),
                    **{'uv' + str(k): np.asarray(value, dtype='<f4') for k, value in model.extraUVs.items()})
                item = dict(id=name, source_class_id='0xa7d3ed6a', source_group=str(pack_path.resolve()),
                    source_group_sha256=pack_hash, source_resource_id=resource_id, source_offset=source_resource.offset,
                    source_bytes=len(raw), source_sha256=hashlib.sha256(raw).hexdigest(), lod=0,
                    vertices=len(vertices), triangles=len(triangles), strict_coincident_triangles=0,
                    bounds_dagor_m=dict(lo=vertices.min(axis=0).tolist(), hi=vertices.max(axis=0).tolist()),
                    declared_bounds_dagor_m=dict(lo=lo.tolist(), hi=hi.tolist()),
                    bound_error_m=error, materials=materials, used_material_ids=sorted(set(material_ids)),
                    file=mesh.relative_to(out).as_posix(), sha256=sha(mesh),
                    coordinates='original Dagor metres, Y up', collision='none_visual_foliage')
                (folder / 'model.json').write_text(json.dumps(item, indent=2) + '\n', encoding='utf-8')
                report['models'].append(item)
                # RandomGrass::resetLayersVB appends _a to the managed T0 name.
                # Opacity is a separate source texture, not the RGB mask's alpha.
                for material_id in item['used_material_ids']:
                    mask_name = item['materials'][material_id]['texture_slots'][0]
                    required_textures.add(mask_name.rstrip('*').split('?')[0] + '_a')
            texture_cache = {t['name']: t for t in json.loads(args.texture_index.read_text(encoding='utf-8'))['exported']}
            pending = set(required_textures)
            for name in sorted(required_textures):
                old = texture_cache.get(name)
                if not old:
                    continue
                original = Path(old['file'])
                if sha(original) != old['sha256']:
                    raise ValueError('changed cached source texture: ' + name)
                target = out / 'textures' / (name + original.suffix)
                shutil.copy2(original, target)
                item = dict(old, original_file=str(original), file=target.relative_to(out).as_posix())
                report['textures'].append(item)
                pending.remove(name)
            best = {}
            roots = [args.game_root / 'content/base/res', args.game_root / 'content.hq/hq_tex/res',
                     args.game_root / 'content.hq/uhq_environment/res']
            for path in sorted({p for root in roots for p in root.glob('*.dxp.bin')}):
                texture_pack = DDSxTexturePack2(str(path))
                for texture in texture_pack.getPackedFiles():
                    if texture.name in pending and (texture.name not in best or texture.getPixelCnt() > best[texture.name].getPixelCnt()):
                        best[texture.name] = texture
            if pending - set(best):
                raise ValueError('missing original foliage textures: ' + repr(sorted(pending - set(best))))
            for name, texture in sorted(best.items()):
                image, floats = decode_texture(texture)
                if image is None or floats is not None:
                    raise ValueError('unexpected floating foliage texture: ' + name)
                target = out / 'textures' / (name + '.png')
                image.save(target)
                report['textures'].append(dict(name=name, source=str(texture.filePath), offset=texture.offset,
                    packed_bytes=texture.size, size=list(image.size), mode=image.mode,
                    file=target.relative_to(out).as_posix(), sha256=sha(target)))
        texture_map = {t['name']: t for t in report['textures']}
        index_spec = texture_map[texture_params['indices']]
        indices = np.asarray(Image.open(out / index_spec['file']))
        if indices.dtype != np.uint8 or indices.ndim != 2 or indices.shape != (4096, 4096) or int(indices.max()) >= 64:
            raise ValueError('expected original 4096-square single-channel biome IDs below 64')
        ids, counts = np.unique(indices, return_counts=True)
        masks = {}
        for index, layer in enumerate(layers):
            eligible = layer_ids(grass, layer)
            key = '_'.join(map(str, eligible))
            if key not in masks:
                mask = np.isin(indices, eligible).astype(np.uint8) * 255
                target = out / 'masks' / ('biomes_' + key + '.png')
                Image.fromarray(mask).save(target)
                pixels = int(np.count_nonzero(mask))
                masks[key] = dict(file=target.relative_to(out).as_posix(), sha256=sha(target),
                    width=4096, height=4096, eligible_pixels=pixels,
                    eligible_area_m2=pixels * 0.25, biome_ids=eligible)
            density = one(layer, 'density', 1.0)
            if not isinstance(density, (int, float)) or not math.isfinite(density) or density <= 0:
                raise ValueError('invalid original grass density')
            report['layers'].append(dict(id='layer_' + str(index).zfill(2), model=one(layer, 'res'),
                source_parameters=layer['params'], source_density=density, source_colors=color_records(layer),
                biome_ids=eligible, eligibility_mask=masks[key],
                density_interpretation='original renderer relative layer density; not UE instances per square metre'))
        report['core_region'] = dict(dagor_xz_min=[0.0, 0.0], dagor_xz_max=[2048.0, 2048.0],
            ros_xy_min=[0.0, -2048.0], ros_xy_max=[2048.0, 0.0],
            source_index_texture=index_spec, source_index_pixel_m=0.5,
            source_uv=dict(u='(DagorX + 16384) / 2048', v='-(DagorZ + 16384) / 2048', address='wrap',
                native_biome_index_offset_m=0.25, offset_note='source landclass index half-texel; preserve source sampling contract'),
            biome_pixel_histogram={str(int(i)): int(n) for i, n in zip(ids, counts)})
        report['grass_weight_source'] = dict(texture=texture_map[texture_params['grass_weight']],
            role='original indexed-landclass grass-weight input; no invented multiplication into biome eligibility',
            interpretation='source custom shader coverage combination requires independent validation')
        report['small_grass_source'] = required_child(grass, 'small_grass')
        report['source_density'] = dict(global_density=one(grass, 'density'), max_radius_m=one(grass, 'maxRadius'),
            fade_delta_m=one(grass, 'fadeDelta'), min_lod_density=one(grass, 'minLodDensity'),
            note='preserve original parameters; UE streaming/population adapter must declare its density conversion')
        report['instance_placement'] = dict(status='not_generated', source='GPU-generated grass layers and masks',
            adaptation='source-rule reconstruction with view-based streaming; original random instance positions are not stored')
        report['status'] = 'prepared_source_inputs_pending_ue_foliage_adapter'
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = str(error)
        raise
    finally:
        (out / 'foliage-source.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    summary = dict(schema='wt_foliage_source_verification_v1', status=report['status'],
        source_config_sha256=config_hash, source_level_sha256=report['source_level_sha256'],
        foliage_source_sha256=sha(out / 'foliage-source.json'), model_count=len(report['models']),
        source_triangles=sum(m['triangles'] for m in report['models']), texture_count=len(report['textures']),
        layer_count=len(report['layers']), mask_count=len(masks), small_grass_types=len(required_child(report['small_grass_source'], 'grass_types')['children']),
        bounds_dagor_xz_m=[[0, 0], [2048, 2048]], generated_instances=0,
        layers=[dict(id=l['id'], model=l['model'], biome_ids=l['biome_ids'], density=l['source_density'],
            eligible_pixels=l['eligibility_mask']['eligible_pixels']) for l in report['layers']],
        original_game_and_extraction='read only')
    (out / 'verification.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
