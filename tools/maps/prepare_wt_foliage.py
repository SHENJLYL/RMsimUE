"""Pack original Flanders grass inputs for view-streamed Landscape Grass.

The independent supplement keeps original meshes, RGB masks, paired opacity,
biome IDs, landclass coverage and source parameters. UE reconstructs placement;
the source renderer's random positions are not stored in the level.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil

import numpy as np
from PIL import Image

from extract_wt_foliage import sha


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def checked_file(base, spec):
    path = (base / spec['file']).resolve(strict=True)
    if not path.is_relative_to(base.resolve()) or sha(path) != spec['sha256']:
        raise ValueError('source file identity differs: ' + str(path))
    return path


def copy_bound(path, output, relative):
    target = output / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    if sha(path) != sha(target):
        raise ValueError('copied source bytes differ')
    return relative, sha(target)


def bilinear_pixels(data, x, y, wrap=False):
    x, y = np.broadcast_arrays(x, y)
    x0, y0 = np.floor(x).astype(np.int32), np.floor(y).astype(np.int32)
    fx, fy = (x - x0).astype(np.float32), (y - y0).astype(np.float32)
    h, w = data.shape[:2]
    xx0, xx1 = (x0 % w, (x0 + 1) % w) if wrap else (np.clip(x0, 0, w-1), np.clip(x0+1, 0, w-1))
    yy0, yy1 = (y0 % h, (y0 + 1) % h) if wrap else (np.clip(y0, 0, h-1), np.clip(y0+1, 0, h-1))
    if data.ndim == 3:
        fx, fy = fx[..., None], fy[..., None]
    a = data[yy0, xx0] * (1-fx) + data[yy0, xx1] * fx
    b = data[yy1, xx0] * (1-fx) + data[yy1, xx1] * fx
    return a * (1-fy) + b * fy


def first(parameters, name, default=None):
    return next((p['value'] for p in parameters if p['name'] == name), default)


def density_contract(model, layer, source):
    # randomGrass.cpp 698-725, 904-912, 1108-1126; randomGrass.h 33-34.
    radius = source['max_radius_m'] + source['fade_delta_m']
    cell_size = 2 * radius / 9
    base = math.floor(radius * radius * source['global_density'])
    requested = math.floor(base * layer['source_density'] / 100 + .5)
    cap = 65534 // model['vertices']
    count = min(requested, cap)
    return dict(reference_quality_multiplier=1.0, graphics_grass_density_multiplier=1.0,
                global_density_multiplier=1.0, source_lod=0, source_grid_size=10,
                source_max_vertices_per_cell=65534, source_vertices_per_instance=model['vertices'],
                source_max_radius_with_fade_m=radius, source_cell_size_m=cell_size,
                requested_candidates_per_cell=requested, capped_candidates_per_cell=count,
                candidate_density_per_m2=count/(cell_size*cell_size),
                ue_density_per_100m2=100*count/(cell_size*cell_size))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--meshes', type=Path, required=True)
    parser.add_argument('--landclass', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    source_path, mesh_path, land_path = (p.resolve(strict=True) for p in (args.source, args.meshes, args.landclass))
    source, mesh_report, land = map(read, (source_path, mesh_path, land_path))
    out = args.output.resolve()
    if out.exists():
        raise ValueError('use a fresh foliage output directory')
    if source['schema'] != 'wt_foliage_source_v1' or mesh_report['status'] != 'passed':
        raise ValueError('source extraction and FBX roundtrip verification are required')
    if source['source_level_sha256'] != land['source_sha256']:
        raise ValueError('foliage and landclass sources differ')
    source_base, mesh_base = source_path.parent, mesh_path.parent
    package = land_path.parent.parent
    textures = {item['name']: item for item in source['textures']}
    source_models = {item['id']: item for item in source['models']}
    source_fbx = {item['id']: item for item in mesh_report['models']}
    if set(source_models) != set(source_fbx) or len(source_models) != 7 or len(source['layers']) != 13:
        raise ValueError('unexpected original Flanders layer/model inventory')
    out.mkdir(parents=True)
    args.evidence.mkdir(parents=True, exist_ok=True)
    source_hashes = {str(path): sha(path) for path in (source_path, mesh_path, land_path)}
    result = dict(schema='wt_source_foliage_v1', version=1, coordinate_system='ros_rh_m',
        source_level_sha256=source['source_level_sha256'], source_config=source['source_config'],
        bounds_m=dict(min=[0, -2048], max=[2048, 0]),
        weight_world_uv=dict(u=[1.0/204800, 0, 0], v=[0, -1.0/204800, 1]),
        color_correction_multiplier=4.59479341998814, alpha_clip_value=1/3,
        alpha_clip_provenance='UE Masked adapter threshold; original random_grass shader threshold is not published',
        models=[], weight_textures=[], layers=[], source_inputs=source_hashes,
        placement=dict(engine='UE LandscapeGrassOutput and GrassType', mode='view_streamed',
                       generated_instances_in_package=0, collision=False,
                       original_random_positions='not serialized in original level',
                       source_rule_reconstruction=True),
        adaptation_scope=dict(
            geometry='original RndGrass LOD0; seven meshes and 56 original triangles',
            coverage='original 0.5m biome-ID texels times bilinear original grass_weight.R and lmap class-1 blend weight',
            color='published billboard-grass mask composition, original layer colors and original macro-land color',
            sampling='negative-V native biome texel centres; original height-dependent four-biome detail blend is not baked',
            unimplemented_source_operations=['microdetail-dependent grass-removal bits',
                'grass-decal channel overrides and terrain displacement', 'small_grass procedural billboard system',
                'Dagor random seed, wind and renderer-specific multi-LOD transitions']),
        source_code=dict(commit=source['official_commit'],
            paired_alpha='prog/gameLibs/render/randomGrass.cpp:802-818,1314-1315',
            mask_color='prog/gameLibs/render/shaders/fast_grass.dshl:218-227',
            correction='prog/daNetGame/shaders/shader_global.dshl:715',
            source_colors='prog/gameLibs/render/randomGrass.cpp:332-339,848-853',
            first_parameter='prog/engine/ioSys/dataBlock/blk_core.cpp:727-753',
            density='prog/gameLibs/render/randomGrass.cpp:698-725,904-912,1108-1126',
            grass_weight='prog/daNetGame/shaders/make_clipmap.dshl:1068-1072'))
    for identity, model in source_models.items():
        part = source_fbx[identity]
        if model['sha256'] != part['source_npz_sha256']:
            raise ValueError('FBX source geometry differs from the final extraction')
        checked_file(source_base, model)
        fbx = checked_file(mesh_base, part)
        filename, digest = copy_bound(fbx, out, 'meshes/' + identity + '.fbx')
        material = model['materials'][model['used_material_ids'][0]]
        name = material['texture_slots'][0].rstrip('*').split('?')[0]
        mask, alpha = textures[name], textures[name + '_a']
        mask_path, alpha_path = checked_file(source_base, mask), checked_file(source_base, alpha)
        mask_file, mask_hash = copy_bound(mask_path, out, 'textures/' + mask_path.name)
        alpha_file, alpha_hash = copy_bound(alpha_path, out, 'textures/' + alpha_path.name)
        ma, aa = np.asarray(Image.open(mask_path)), np.asarray(Image.open(alpha_path))
        if ma.shape[:2] != aa.shape or aa.dtype != np.uint8 or aa.min() == aa.max():
            raise ValueError('paired grass opacity must be original, aligned and nonconstant')
        result['models'].append(dict(id=identity, file=filename, sha256=digest, fbx_import_scale=1,
            triangles=part['triangles'], vertices=part['vertices'], bounds_m=part['bounds_m'],
            source_resource_sha256=model['source_sha256'], source_npz_sha256=model['sha256'],
            material=dict(shader='random_grass', texture=mask_file, texture_sha256=mask_hash,
                texture_semantics='linear R/B tint masks and G brightness/color interpolation',
                alpha_texture=alpha_file, alpha_texture_sha256=alpha_hash,
                alpha_semantics='separate original managed texture named T0 + _a; linear R',
                alpha_statistics=dict(min=int(aa.min()), max=int(aa.max()), mean=float(aa.mean())))))
    color_spec = land['textures']['LC1']
    color_path = checked_file(package, color_spec)
    filename, digest = copy_bound(color_path, out, 'textures/' + color_path.name)
    result['land_color'] = dict(texture=filename, texture_sha256=digest, srgb=True,
        world_uv=dict(u=[1.0/204800, 0, 0], v=[0, -1.0/204800, 1]), address='wrap',
        source='original Flanders macro texture; source runtime detail/decal color is not baked',
        selected_source=color_spec['source'], resolution=color_spec['size'])
    index_spec = source['core_region']['source_index_texture']
    grass_spec = source['grass_weight_source']['texture']
    index_path, grass_path = checked_file(source_base, index_spec), checked_file(source_base, grass_spec)
    atlas_spec = land['textures']['WT_LandWeights']
    atlas_path = checked_file(package, atlas_spec)
    indices = np.asarray(Image.open(index_path))
    grass = np.asarray(Image.open(grass_path), dtype=np.float32)[..., 0] / 255
    atlas = np.asarray(Image.open(atlas_path), dtype=np.float32) / 255
    if indices.shape != (4096, 4096) or grass.shape != (2048, 2048) or atlas.shape != (272, 7616, 3):
        raise ValueError('source mask or atlas dimensions differ')
    for path in (index_path, grass_path, atlas_path, color_path):
        source_hashes[str(path)] = sha(path)
    coverage = np.empty((4096, 4096), dtype=np.float32)
    land_coverage = np.empty_like(coverage)
    cells = {tuple(c['xy']): c for c in land['cells']}
    # PNG row zero is northern source Z edge; keep the source negative-V layout.
    xx = (np.arange(4096, dtype=np.float32) + .5) * .5
    for first_row in range(0, 4096, 128):
        rows = np.arange(first_row, first_row + 128, dtype=np.float32)
        zz = 2048 - (rows + .5) * .5
        lc = np.zeros((128, 4096), dtype=np.float32)
        for cell_x in (16, 17):
            columns = np.where((xx / 1024 + 16).astype(np.int32) == cell_x)[0]
            for cell_y in (16, 17):
                local_rows = np.where((zz / 1024 + 16).astype(np.int32) == cell_y)[0]
                if not len(local_rows) or not len(columns):
                    continue
                cell = cells[(cell_x, cell_y)]
                count = cell['blend_count']
                classes = cell['class_ids'][:count]
                if 1 not in classes:
                    continue
                slot = classes.index(1)
                fx = (xx[columns] / 1024 + 16 - cell_x)[None, :]
                fy = (zz[local_rows] / 1024 + 16 - cell_y)[:, None]
                samples = []
                for page in cell['pages'][:math.ceil(max(0, count-1)/3)]:
                    px, py = (page % 112)*68 + 2 + fx*64 - .5, (page//112)*68 + 2 + fy*64 - .5
                    samples.append(bilinear_pixels(atlas, px, py))
                previous = np.concatenate(samples, axis=2)[..., :count-1] if samples else np.empty((len(local_rows), len(columns), 0))
                weight = np.maximum(0, 1-previous.sum(axis=2)) if slot == count-1 else previous[..., slot]
                lc[np.ix_(local_rows, columns)] = weight
        # Source grass-weight texture uses the same negative-V landclass UV.
        gw = bilinear_pixels(grass, (np.arange(4096, dtype=np.float32)[None, :] + .5)*.5 - .5,
                             (rows[:, None] + .5)*.5 - .5, wrap=True)
        land_coverage[first_row:first_row+128] = lc
        coverage[first_row:first_row+128] = lc * gw
    if not np.isfinite(coverage).all() or coverage.min() < 0 or coverage.max() > 1.000001:
        raise ValueError('source grass coverage is invalid')
    layers_stats = []
    for texture_no in range(4):
        packed = np.zeros((4096, 4096, 4), dtype=np.uint8)
        for channel in range(4):
            layer_no = texture_no*4 + channel
            if layer_no >= len(source['layers']):
                continue
            layer = source['layers'][layer_no]
            eligibility = np.isin(indices, layer['biome_ids'])
            values = np.where(eligibility, coverage, 0)
            packed[..., channel] = np.rint(values*255).astype(np.uint8)
            model = source_models[layer['model']]
            parameters = layer['source_parameters']
            density = density_contract(model, layer, source['source_density'])
            horizontal = [first(parameters, 'horMinScale', 1.0), first(parameters, 'horScale', 1.0)]
            vertical = [first(parameters, 'verMinScale', 1.0), first(parameters, 'verScale', 1.0)]
            radius = first(parameters, 'radiusMul', 1.0)
            colors = {}
            for name in 'rgb':
                colors[name] = {}
                for endpoint, key in (('min', 'from'), ('max', 'to')):
                    color = layer['source_colors'].get('color_mask_' + name + '_' + key)
                    if color is None:
                        default = [0,0,0,255] if endpoint == 'min' else ([128,128,128,255] if name == 'g' else [255,255,255,255])
                        rgba = default
                    else:
                        rgba = color['rgba']
                    colors[name][endpoint] = [c/255 for c in rgba]
            stats = dict(id=layer['id'], eligible_pixels=int(eligibility.sum()),
                nonzero_weight_pixels=int(np.count_nonzero(packed[..., channel])),
                max_weight=float(values.max()), mean_weight=float(values.mean()),
                weighted_area_m2=float(values.sum(dtype=np.float64)*.25),
                uint8_max_error=float(np.max(np.abs(packed[..., channel].astype(np.float32)/255-values))))
            layers_stats.append(stats)
            result['layers'].append(dict(id=layer['id'], model_id=layer['model'],
                weight_texture='WT_GrassWeights' + str(texture_no), weight_channel=channel,
                density_per_100m2=density['ue_density_per_100m2'],
                scale_x=sorted(horizontal), scale_y=sorted(horizontal), scale_z=sorted(vertical),
                scaling='lock_xy', start_cull_distance_cm=source['source_density']['max_radius_m']*radius*100,
                end_cull_distance_cm=(source['source_density']['max_radius_m']+source['source_density']['fade_delta_m'])*radius*100,
                collision=False, color_masks=colors, source=dict(biome_ids=layer['biome_ids'],
                    density=layer['source_density'], horizontal_scale=horizontal, vertical_scale=vertical,
                    colors=layer['source_colors'], parameters=parameters, density_conversion=density),
                weight_statistics=stats))
        relative = 'weights/WT_GrassWeights' + str(texture_no) + '.png'
        target = out/relative
        target.parent.mkdir(exist_ok=True)
        Image.fromarray(packed).save(target)
        if not np.array_equal(np.asarray(Image.open(target)), packed):
            raise ValueError('packed grass PNG did not roundtrip exactly')
        result['weight_textures'].append(dict(id='WT_GrassWeights'+str(texture_no), file=relative,
            sha256=sha(target), width=4096, height=4096, channels=4, srgb=False, address='clamp'))
    result['limitations'] = result['adaptation_scope']['unimplemented_source_operations'] + [
        result['adaptation_scope']['sampling'], result['land_color']['source'],
        result['alpha_clip_provenance']]
    result['status'] = 'prepared_source_rule_adapter_pending_native_validation'
    save(out/'foliage.json', result)
    samples = []
    for x, z in [(1028,1020),(100,1400),(1700,500),(800,1700)]:
        col = min(4095, int(x*2)); row = min(4095, int((2048-z)*2))
        samples.append(dict(dagor_xz_m=[x,z], png_xy=[col,row], biome_id=int(indices[row,col]),
            landclass_weight=float(land_coverage[row,col]), combined_grass_weight=float(coverage[row,col]),
            eligible_layers=[l['id'] for l in source['layers'] if int(indices[row,col]) in l['biome_ids']]))
    report = dict(schema='wt_foliage_preparation_verification_v1', status='passed',
        scope='source bytes, mesh FBX roundtrip, source-mask preparation and contract; native UE validation separate',
        foliage_file=str(out/'foliage.json'), foliage_sha256=sha(out/'foliage.json'),
        source_inputs=source_hashes, source_model_count=7, source_triangles=56, layer_count=13,
        packed_weight_textures=4, coverage_resolution=[4096,4096], coverage_pixel_m=.5,
        source_inputs_unchanged=all(sha(Path(path))==digest for path,digest in source_hashes.items()),
        mesh_roundtrips=mesh_report['roundtrips'], layers=layers_stats, samples=samples,
        max_weight_quantization_error=max(item['uint8_max_error'] for item in layers_stats),
        adaptation_scope=result['adaptation_scope'], generated_instances=0)
    if not report['source_inputs_unchanged']:
        raise ValueError('source input changed while preparing foliage')
    save(args.evidence/'verification.json', report)
    print(json.dumps(dict(status=report['status'], foliage_file=report['foliage_file'],
        foliage_sha256=report['foliage_sha256'], models=7, triangles=56, layers=13,
        weight_textures=4, samples=samples), indent=2), flush=True)


if __name__ == '__main__':
    main()
