"""Export the existing CC0 tree without destructive simplification.

Run in background Blender for CPU geometry conversion only; no renderer is used.
Source files and active school layout/assets stay unchanged. All source surfaces, material slots, normals and UVs are retained.
Only exactly proven coincident reverse duplicates in a double-sided material
may be deduplicated by Blender; source indices and attributes remain recorded. KHR_texture_transform is baked into
OBJ UVs because OBJ has no material-specific UV-transform graph.
"""
import argparse
import collections
import hashlib
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def source_duplicate_faces(source, gltf):
    import numpy as np
    buffers = [(source.parent / entry['uri']).read_bytes() for entry in gltf['buffers']]
    def accessor(index):
        entry = gltf['accessors'][index]
        if 'sparse' in entry:
            raise ValueError('Sparse source accessor needs a separate decoder')
        view = gltf['bufferViews'][entry['bufferView']]
        dtype = {5126: np.float32, 5125: np.uint32, 5123: np.uint16}[entry['componentType']]
        width = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}[entry['type']]
        item = np.dtype(dtype).itemsize
        return np.ndarray((entry['count'], width), dtype=dtype, buffer=buffers[view['buffer']],
            offset=view.get('byteOffset', 0) + entry.get('byteOffset', 0),
            strides=(view.get('byteStride', width * item), item))
    records = []
    for mesh_id, mesh in enumerate(gltf['meshes']):
        for primitive_id, primitive in enumerate(mesh['primitives']):
            indices = accessor(primitive['indices']).reshape(-1, 3)
            canonical = np.sort(indices, axis=1)
            unique, inverse, counts = np.unique(canonical, axis=0, return_inverse=True, return_counts=True)
            for entry_id in np.flatnonzero(counts > 1):
                triangle_ids = np.flatnonzero(inverse == entry_id)
                material = gltf['materials'][primitive['material']]
                if len(triangle_ids) != 2 or not material.get('doubleSided', False):
                    raise ValueError('Unproven source duplicate-face pattern')
                first, second = indices[triangle_ids]
                reverse = list(reversed(first.tolist()))
                if second.tolist() not in [reverse[i:] + reverse[:i] for i in range(3)]:
                    raise ValueError('Duplicate source face is not a double-sided reverse pair')
                vertex_ids = first.tolist()
                records.append({'mesh': mesh_id, 'primitive': primitive_id,
                    'material': material['name'], 'source_triangle_ids': triangle_ids.tolist(),
                    'source_index_triples': indices[triangle_ids].tolist(),
                    'positions': accessor(primitive['attributes']['POSITION'])[vertex_ids].tolist(),
                    'normals': accessor(primitive['attributes']['NORMAL'])[vertex_ids].tolist(),
                    'texcoord0': accessor(primitive['attributes']['TEXCOORD_0'])[vertex_ids].tolist(),
                    'double_sided_material': True,
                    'policy': 'Blender removes exactly one coincident reverse face; double-sided surface, source vertex/normal/UV identities and both source indices are retained as proof.'})
    return records


def fetch_textures(out):
    import urllib.request
    api_url = 'https://api.polyhaven.com/files/island_tree_02'
    request = urllib.request.Request(api_url, headers={'User-Agent': 'RMSimUE5 source fidelity audit'})
    with urllib.request.urlopen(request, timeout=45) as response:
        api = json.load(response)
    records = {}
    needed = {('island_tree_02' + suffix + '_' + kind + '_2k.jpg')
              for suffix in ('', '_branches', '_leaves')
              for kind in ('diff', 'nor_gl', 'arm')}
    for relative, rec in api['gltf']['2k']['gltf']['include'].items():
        name = Path(relative).name
        if name in needed:
            records[name] = dict(rec)
    if set(records) != needed:
        raise ValueError('Official API does not contain every required 2K tree map')
    records['island_tree_02_leaves_alpha_2k.png'] = dict(api['leaves_alpha']['2k']['png'])
    if sum(rec['size'] for rec in records.values()) > 30 * 1024 * 1024:
        raise ValueError('Tree texture download exceeds this conversion budget')
    directory = out / 'textures'
    directory.mkdir()
    files = {}
    for name, rec in records.items():
        if not rec['url'].startswith('https://dl.polyhaven.org/file/ph-assets/Models/'):
            raise ValueError('Unexpected source asset host')
        path = directory / name
        partial = path.with_suffix(path.suffix + '.partial')
        md5 = hashlib.md5()
        request = urllib.request.Request(rec['url'], headers={'User-Agent': 'RMSimUE5 source fidelity importer'})
        with urllib.request.urlopen(request, timeout=120) as response, partial.open('wb') as writer:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                writer.write(block)
                md5.update(block)
        if partial.stat().st_size != rec['size'] or md5.hexdigest() != rec['md5']:
            raise ValueError('Official source hash/size mismatch: ' + name)
        partial.replace(path)
        image = bpy.data.images.load(str(path), check_existing=False)
        size = list(image.size)
        bpy.data.images.remove(image)
        if size != [2048, 2048]:
            raise ValueError('Source map is not 2K: ' + name)
        files['textures/' + name] = dict(rec, sha256=sha(path), bytes=path.stat().st_size, dimensions=size)
        print('TREE_TEXTURE_VERIFIED', name, rec['size'], flush=True)
    source = {'schema': 'rmsim_school_tree_2k_source_v1', 'license': 'CC0-1.0',
              'page': 'https://polyhaven.com/a/island_tree_02', 'api': api_url,
              'files': files, 'raster_changes': False,
              'alpha_note': 'Unmodified source PNG sampled independently in the UE opacity mask.'}
    (out / 'SOURCE-textures.json').write_text(json.dumps(source, indent=2) + '\n', encoding='utf-8')
    materials = {}
    for suffix in ('', '_branches', '_leaves'):
        name = 'island_tree_02' + suffix
        materials[name] = {'base_color_factor': [1., 1., 1., 1.], 'metallic_factor': 0.,
                           'roughness_factor': 1., 'semantic': 'source',
                           'base_color_texture': 'textures/' + name + '_diff_2k.jpg',
                           'normal_texture': 'textures/' + name + '_nor_gl_2k.jpg',
                           'normal_convention': 'OpenGL',
                           'normal_scale': .5 if suffix == '_branches' else 1.,
                           'arm_texture': 'textures/' + name + '_arm_2k.jpg',
                           'arm_channels': {'ambient_occlusion': 'R', 'roughness': 'G', 'metallic': 'B'},
                           'double_sided': True, 'alpha_mode': 'OPAQUE'}
    materials['island_tree_02_leaves'].update(alpha_mode='MASK', alpha_cutoff=.45,
          alpha_texture='textures/island_tree_02_leaves_alpha_2k.png',
          shading_model='TwoSidedFoliage', subsurface_color_factor=[.35, .35, .35])
    return files, materials


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args(sys.argv[sys.argv.index('--') + 1:])
    root, out = args.root.resolve(), args.out.resolve()
    source = root / 'sources/polyhaven/island_tree_02/island_tree_02_1k.gltf'
    if out.exists():
        raise FileExistsError('Use a fresh export destination: ' + str(out))
    gltf = json.loads(source.read_text(encoding='utf-8'))
    source_files = {source.name: sha(source)}
    for entry in gltf['buffers']:
        path = (source.parent / entry['uri']).resolve()
        if not path.is_relative_to(source.parent):
            raise ValueError('Source buffer escaped model directory')
        source_files[entry['uri']] = sha(path)
    expected_by_material = collections.Counter()
    for mesh in gltf['meshes']:
        for primitive in mesh['primitives']:
            if primitive.get('mode', 4) != 4:
                raise ValueError('Only original triangle primitives are supported')
            count = gltf['accessors'][primitive['indices']]['count']
            if count % 3:
                raise ValueError('Source index count is not divisible by three')
            expected_by_material[gltf['materials'][primitive['material']]['name']] += count // 3
    duplicate_faces = source_duplicate_faces(source, gltf)
    expected_imported = expected_by_material.copy()
    for duplicate in duplicate_faces:
        expected_imported[duplicate['material']] -= 1
    transforms = {}
    for material in gltf['materials']:
        maps = [material.get('normalTexture'),
                material.get('pbrMetallicRoughness', {}).get('baseColorTexture'),
                material.get('pbrMetallicRoughness', {}).get('metallicRoughnessTexture')]
        found = []
        for item in maps:
            if not item:
                continue
            transform = item.get('extensions', {}).get('KHR_texture_transform', {})
            if item.get('texCoord', 0) != 0 or transform.get('texCoord', 0) != 0:
                raise ValueError('Unexpected nonzero source UV set')
            found.append({'offset': transform.get('offset', [0., 0.]),
                          'scale': transform.get('scale', [1., 1.]),
                          'rotation': transform.get('rotation', 0.)})
        if found and any(x != found[0] for x in found):
            raise ValueError('Different source texture transforms require separate UV sets')
        transforms[material['name']] = found[0] if found else {
            'offset': [0., 0.], 'scale': [1., 1.], 'rotation': 0.}
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(source))
    objects = [o for o in bpy.context.scene.objects if o.type == 'MESH']
    if not objects:
        raise RuntimeError('Source tree import produced no meshes')
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    if len(objects) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    obj.name = 'RMExteriorTree'
    obj.data.name = 'RMExteriorTree'
    if obj.modifiers:
        raise RuntimeError('Source unexpectedly has geometry modifiers')
    obj.scale = tuple(value * 2.30 for value in obj.scale)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bpy.context.view_layer.update()
    lower = min((obj.matrix_world @ Vector(c)).z for c in obj.bound_box)
    obj.location.z -= lower
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)
    bpy.context.view_layer.update()
    actual_by_material = collections.Counter()
    uv = obj.data.uv_layers.active
    if uv is None:
        raise ValueError('Source tree lost its UV layer')
    baked_loop_count = collections.Counter()
    uv_checks = []
    for polygon in obj.data.polygons:
        if polygon.loop_total != 3:
            raise ValueError('Importer changed source triangle topology')
        name = obj.data.materials[polygon.material_index].name
        actual_by_material[name] += 1
        transform = transforms[name]
        sx, sy = transform['scale']
        ox, oy = transform['offset']
        angle = transform['rotation']
        if [sx, sy, ox, oy, angle] == [1., 1., 0., 0., 0.]:
            continue
        cosine, sine = math.cos(angle), math.sin(angle)
        for loop_id in polygon.loop_indices:
            u, v = uv.data[loop_id].uv
            # Blender/OBJ use V=1-glTF_V. Apply KHR transform in source space,
            # then restore OBJ V, including the translation term exactly.
            source_u, source_v = float(u), 1. - float(v)
            transformed_u = ox + cosine * sx * source_u - sine * sy * source_v
            transformed_v = oy + sine * sx * source_u + cosine * sy * source_v
            uv.data[loop_id].uv = (transformed_u, 1. - transformed_v)
            baked_loop_count[name] += 1
            if len(uv_checks) < 3:
                uv_checks.append({'material': name, 'source_gltf_uv': [source_u, source_v],
                                  'obj_baked_uv': list(uv.data[loop_id].uv)})
    if actual_by_material != expected_imported:
        raise ValueError('Original source topology was not preserved: ' +
                         repr([dict(expected_imported), dict(actual_by_material)]))
    out.mkdir(parents=True)
    texture_files, source_materials = fetch_textures(out)
    path = out / 'RMExteriorTree.obj'
    bpy.ops.wm.obj_export(filepath=str(path), export_selected_objects=True,
                         forward_axis='Y', up_axis='Z', global_scale=1.,
                         apply_modifiers=False, export_uv=True, export_normals=True,
                         export_materials=True, export_triangulated_mesh=True,
                         path_mode='RELATIVE')
    bounds_min = [min((obj.matrix_world @ Vector(c))[i] for c in obj.bound_box) for i in range(3)]
    bounds_max = [max((obj.matrix_world @ Vector(c))[i] for c in obj.bound_box) for i in range(3)]
    counts = collections.Counter()
    exported_by_material = collections.Counter()
    current = None
    with path.open('r', encoding='utf-8') as stream:
        for line in stream:
            operation = line.partition(' ')[0]
            if operation in ('v', 'vt', 'vn', 'f'):
                counts[operation] += 1
            if operation == 'usemtl':
                current = line.split()[1]
            if operation == 'f':
                if len(line.split()) != 4:
                    raise ValueError('Exporter wrote a non-triangle face')
                exported_by_material[current] += 1
    if exported_by_material != expected_imported:
        raise ValueError('Export did not preserve every source triangle/material')
    for name, expected_hash in source_files.items():
        path_in = source if name == source.name else source.parent / name
        if sha(path_in) != expected_hash:
            raise RuntimeError('Source changed during conversion')
    record = {'asset_name': obj.name, 'file': path.name, 'bounds_min_m': bounds_min,
              'bounds_max_m': bounds_max, 'triangles': counts['f'],
              'material_slots': [x.name for x in obj.data.materials],
              'sha256': sha(path), 'bytes': path.stat().st_size}
    report = {'schema': 'rmsim_school_tree_fidelity_v1', 'status': 'passed',
              'source': str(source), 'source_files_sha256': source_files,
              'source_license': 'CC0-1.0', 'scale': 2.30,
              'decimation': False, 'source_triangles': sum(expected_by_material.values()),
              'source_triangles_by_material': dict(expected_by_material),
              'proven_source_reverse_duplicate_faces': duplicate_faces,
              'proven_reverse_duplicate_count_removed': len(duplicate_faces),
              'expected_unique_surface_triangles': sum(expected_imported.values()),
              'exported_triangles_by_material': dict(exported_by_material),
              'obj_counts': dict(counts), 'uv_transforms_baked': transforms,
              'baked_uv_loop_count': dict(baked_loop_count), 'uv_examples': uv_checks,
              'meshes': {'RMExteriorTree': record},
              'files': texture_files, 'materials': source_materials,
              'blender_version': bpy.app.version_string,
              'script_sha256': sha(Path(__file__).resolve()),
              'scope': 'CPU source geometry and UV conversion only. Active UE assets/layout unchanged; native UE import and visual quality require separate acceptance.'}
    (out / 'prepared.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('SCHOOL_TREE_FIDELITY_PASSED', json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
