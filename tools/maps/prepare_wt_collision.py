"""Blender: derive Flanders sleeper/curb collision from source geometry.

Original extraction, scene-source, visuals, and World remain read-only. Source
triangles share a static BVH; four convex curb variants share an eight-point
convex. Full affine transforms are baked into small, local-coordinate cells.
All generated meshes/instance streams stay in the local ignored map package.
"""
import argparse
import collections
import hashlib
import itertools
import json
import math
from pathlib import Path
import struct
import sys
import bpy
import numpy as np
from io_scene_fbx import parse_fbx

C = np.array(((1, 0, 0), (0, 0, -1), (0, 1, 0)), dtype=np.float64)
COUNTS = {
    'russia_rails_sleeper_a': 14349, 'rail_crosstie': 37,
    'pavement_border_a': 1013, 'pavement_border_a_secondlayer': 334,
    'pavement_border_a_white_secondlayer': 514,
    'pavement_border_b': 651, 'pavement_border_b_secondlayer': 294,
    'pavement_border_b_white_secondlayer': 423,
    'pavement_border_c': 355, 'pavement_border_d': 242,
    'pavement_border_e': 226, 'pavement_border_g': 4,
    'sicily_pavement_border_a': 4, 'sicily_pavement_border_b': 8,
}
CONVEX = {'pavement_border_a_white_secondlayer',
          'pavement_border_b_white_secondlayer',
          'sicily_pavement_border_a', 'sicily_pavement_border_b'}
PREFIX = '/Game/RMSim/Maps/wt_vlaanderen/GeneratedCollision/Meshes/'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path, algorithm='sha256'):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def bounds(v):
    return dict(lo=v.min(axis=0).tolist(), hi=v.max(axis=0).tolist())


def child(node, name):
    entries = [item for item in node.elems if item.id == name]
    if len(entries) != 1:
        raise ValueError('FBX node count: ' + repr(name))
    return entries[0]


def canonical_faces(points):
    order = np.lexsort((points[:, :, 2], points[:, :, 1], points[:, :, 0]), axis=1)
    p = np.take_along_axis(points, order[:, :, None], axis=1)
    p[p == 0] = 0
    return np.sort(np.ascontiguousarray(p).reshape(-1, 9).view('V72').ravel())


def verify_fbx(path, vertices, faces):
    root, version = parse_fbx.parse(str(path))
    geometries = [x for x in child(root, b'Objects').elems
                  if x.id == b'Geometry' and x.props[2] == b'Mesh']
    if len(geometries) != 1:
        raise ValueError('FBX must contain one geometry')
    geometry = geometries[0]
    actual = np.asarray(child(geometry, b'Vertices').props[0], dtype=np.float64).reshape(-1, 3)
    encoded = np.asarray(child(geometry, b'PolygonVertexIndex').props[0], dtype=np.int64)
    ends = np.flatnonzero(encoded < 0)
    if not len(ends) or not np.all(np.diff(np.r_[-1, ends]) == 3) or ends[-1] != len(encoded)-1:
        raise ValueError('FBX must retain source triangles')
    triangles = encoded.copy()
    triangles[triangles < 0] = -triangles[triangles < 0]-1
    triangles = triangles.reshape(-1, 3)
    if len(triangles) != len(faces) or len(actual) != len(vertices):
        raise ValueError('FBX/source array counts differ')
    wanted = canonical_faces(vertices.astype(np.float64)[faces])
    for axes in itertools.permutations(range(3)):
        for signs in itertools.product((-1., 1.), repeat=3):
            mapped = actual[:, axes] * signs
            if np.array_equal(canonical_faces(mapped[triangles]), wanted):
                return dict(vertices=len(actual), triangles=len(triangles),
                            exact_float32_source_faces=True,
                            binary_fbx_version=int(version), axis_permutation=list(axes),
                            axis_signs=list(signs))
    raise ValueError('binary FBX geometry does not match every source triangle: ' + str(path))


def write_fbx(path, name, vertices, faces):
    vertices = np.asarray(vertices, dtype=np.float32)
    faces = np.asarray(faces, dtype=np.int32)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices.tolist(), [], faces.tolist())
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.fbx(
        filepath=str(path), use_selection=True, object_types={'MESH'},
        axis_forward='X', axis_up='Z', apply_unit_scale=True,
        apply_scale_options='FBX_SCALE_UNITS', use_mesh_modifiers=False,
        mesh_smooth_type='OFF', add_leaf_bones=False, bake_anim=False,
        path_mode='AUTO')
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.meshes.remove(mesh)
    return verify_fbx(path, vertices, faces)


def write_rmi(path, rows):
    array = np.asarray(rows, dtype='<f4').reshape(-1, 10)
    if not len(array) or not np.all(np.isfinite(array)):
        raise ValueError('invalid instance rows')
    path.write_bytes(b'RMI1' + struct.pack('<I', len(array)) + array.tobytes())


def transformed_bounds(v, rows):
    corners = np.array(list(itertools.product(*zip(v.min(axis=0), v.max(axis=0)))))
    lows, highs = [], []
    for row in rows:
        x, y, z, w = row[3:7] / np.linalg.norm(row[3:7])
        rotation = np.array([
            [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
            [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
            [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])
        points = (corners * row[7:10]) @ rotation.T + row[:3]
        lows.append(points.min(axis=0))
        highs.append(points.max(axis=0))
    return dict(lo=np.min(lows, axis=0).tolist(), hi=np.max(highs, axis=0).tolist())


def cell_name(x, y):
    return ('p' if x >= 0 else 'm') + str(abs(x)) + '_' + ('p' if y >= 0 else 'm') + str(abs(y))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scene-source', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evidence-root', type=Path, required=True)
    parser.add_argument('--cell-size-m', type=int, default=32, choices=(16, 32, 64))
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    scene_file = args.scene_source.resolve(strict=True)
    package = scene_file.parent
    source = args.source_root.resolve(strict=True)
    out = args.output.resolve()
    evidence = args.evidence_root.resolve()
    if out.exists() or evidence.exists():
        raise ValueError('use fresh output and evidence directories')
    if out != (package/'generated-collision').resolve():
        raise ValueError('generated collision must be the dedicated package supplement')
    manifest_bytes = scene_file.read_bytes()
    manifest = json.loads(manifest_bytes.decode('utf-8-sig'))
    if manifest['id'] != 'wt_vlaanderen' or manifest['schema'] != 'rm_scene_map_source_v1':
        raise ValueError('this recipe is for the verified Flanders source')
    out.mkdir(parents=True)
    evidence.mkdir(parents=True)
    (out/'meshes').mkdir()
    (out/'instances').mkdir()
    (out/'source-bindings').mkdir()
    recipe = dict(schema='wt_static_collision_recipe_v1', version=1,
                  models=COUNTS, cell_size_m=args.cell_size_m,
                  matrix_dagor_to_ros=C.tolist(),
                  triangle_policy='source triangles, shared static BVH, no decimation',
                  convex_models=sorted(CONVEX),
                  affine_policy='complete source affine matrix, local spatial-cell coordinates',
                  runtime='rig and pinned spawn; query range plus preload margin and hysteresis')
    recipe_hash = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    save(out/'recipe.json', recipe)
    specs = {m['source']: m for m in manifest['meshes'] if m.get('source') in COUNTS}
    affine_file = package/'affine-bake-map.json'
    affine = read(affine_file)['batches']
    affine_by_name = collections.defaultdict(list)
    for batch in affine:
        for index, item in enumerate(batch['instances']):
            if item['model'] in COUNTS:
                affine_by_name[item['model']].append(
                    dict(item, original_tile=batch['tile'], original_tile_index=index))
    models, aliases, meshes, source_rows, records, counts = {}, {}, [], {}, [], {}
    geometry_ids = {}
    for name in sorted(COUNTS):
        folder = source/'models-all-07'/name
        metadata = read(folder/'model.json')
        if sha(folder/'mesh.npz') != metadata['mesh_sha256']:
            raise ValueError('original extracted NPZ hash differs: ' + name)
        with np.load(folder/'mesh.npz') as archive:
            vertices = archive['vertices'].astype(np.float64)
            faces = archive['triangles'].astype(np.int32)
            normals = archive['normals'].astype(np.float64)
        cross = np.cross(vertices[faces[:, 1]]-vertices[faces[:, 0]],
                         vertices[faces[:, 2]]-vertices[faces[:, 0]])
        if np.any(np.linalg.norm(cross, axis=1) == 0):
            raise ValueError('source triangle has zero area: ' + name)
        flip = np.einsum('ij,ij->i', cross, normals[faces].mean(axis=1)) < -1e-10
        faces[flip] = faces[flip][:, [0, 2, 1]]
        converted = (vertices @ C.T).astype(np.float32)
        geometry_hash = hashlib.sha256(
            converted.astype('<f4').tobytes() + faces.astype('<i4').tobytes()).hexdigest()
        mode = 'convex' if name in CONVEX else 'triangle_bvh'
        models[name] = dict(vertices=vertices, faces=faces, converted=converted)
        if geometry_hash not in geometry_ids:
            identity = 'gc_' + name
            geometry_ids[geometry_hash] = identity
            file = out/'meshes'/(identity+'.fbx')
            fbx_proof = write_fbx(file, identity, converted, faces)
            spec = dict(id=identity, file='meshes/'+file.name,
                        asset=PREFIX+identity+'.'+identity,
                        sha256=sha(file), mode=mode, vertices=len(converted),
                        triangles=len(faces), bounds_m=bounds(converted),
                        geometry_sha256=geometry_hash, source_models=[name])
            if mode == 'convex':
                unique = np.unique(converted, axis=0)
                if len(unique) != 8:
                    raise ValueError('convex recipe requires the eight original corners')
                spec['convex_vertices_m'] = unique.tolist()
            meshes.append(spec)
            records.append(dict(model=name, generated_fbx=fbx_proof))
        else:
            identity = geometry_ids[geometry_hash]
            mesh = next(x for x in meshes if x['id'] == identity)
            if mesh['mode'] != mode:
                raise ValueError('geometry alias policy differs')
            mesh['source_models'].append(name)
        aliases[name] = identity
        source_rows[name] = np.empty((0, 10), dtype=np.float32)
        binding = dict(model=name, source=metadata['source'],
                       source_npz_sha256=metadata['mesh_sha256'],
                       model_json_sha256=sha(folder/'model.json'),
                       source_vertices=len(vertices), source_triangles=len(faces),
                       corrected_winding_faces=int(flip.sum()))
        if name in specs:
            spec = specs[name]
            fbx = package/spec['file']
            if sha(fbx) != spec['sha256']:
                raise ValueError('prepared original visual differs: ' + name)
            binding['original_visual_fbx_sha256'] = spec['sha256']
            binding['original_visual_geometry'] = verify_fbx(fbx, converted, faces)
            rmi = package/spec['instances_file']
            raw = rmi.read_bytes()
            rows = np.frombuffer(raw[8:], dtype='<f4').reshape(-1, 10).copy()
            if raw[:4] != b'RMI1' or len(rows) != struct.unpack_from('<I', raw, 4)[0]:
                raise ValueError('source RMI identity/count')
            source_rows[name] = rows
            binding.update(original_rmi_file=spec['instances_file'],
                           original_rmi_sha256=sha(rmi), rigid_instances=len(rows))
        counts[name] = len(source_rows[name]) + len(affine_by_name[name])
        if counts[name] != COUNTS[name]:
            raise ValueError('source coverage count differs: ' + name)
        binding.update(source_instances=counts[name],
                       affine_instances=len(affine_by_name[name]),
                       master_id=identity)
        save(out/'source-bindings'/(name+'.json'), binding)
    cells = []
    def add_cell(identity, mesh_id, rows, source_count, world_bounds, binding_file=None):
        file = out/'instances'/(identity+'.rmi')
        write_rmi(file, rows)
        record = dict(id=identity, mesh_id=mesh_id, instances_file='instances/'+file.name,
                      sha256=sha(file), sha1=sha(file, 'sha1'),
                      instances=len(rows), source_instances=source_count,
                      bounds_m=world_bounds)
        if binding_file:
            record.update(source_indices_file=str(binding_file.relative_to(out)).replace('\\', '/'),
                          source_indices_sha256=sha(binding_file))
        cells.append(record)
    for name, rows in source_rows.items():
        groups = collections.defaultdict(list)
        for index, row in enumerate(rows):
            groups[(math.floor(float(row[0])/args.cell_size_m),
                    math.floor(float(row[1])/args.cell_size_m))].append(index)
        for key, indices in sorted(groups.items()):
            identity = 'rigid_'+name+'_'+cell_name(*key)
            selected = rows[indices].astype(np.float64)
            index_file = out/'source-bindings'/(identity+'.u32')
            index_file.write_bytes(np.asarray(indices, dtype='<u4').tobytes())
            add_cell(identity, aliases[name], selected, len(indices),
                     transformed_bounds(models[name]['converted'], selected), index_file)
    batches = collections.defaultdict(list)
    for name, entries in affine_by_name.items():
        for item in entries:
            position = C @ np.asarray(item['translation_dagor_m'], dtype=np.float64)
            key = tuple(math.floor(float(x)/args.cell_size_m) for x in position[:2])
            batches[key].append(item)
    max_affine_error = 0.
    for key, entries in sorted(batches.items()):
        identity = 'gc_affine_'+cell_name(*key)
        origin = np.array([key[0]*args.cell_size_m, key[1]*args.cell_size_m, 0.])
        points, triangles, offset = [], [], 0
        for item in entries:
            data = models[item['model']]
            matrix = np.asarray(item['matrix3x3'], dtype=np.float64)
            position = np.asarray(item['translation_dagor_m'], dtype=np.float64)
            world = data['vertices'] @ (C @ matrix).T + C @ position
            local = (world-origin).astype(np.float32)
            max_affine_error = max(max_affine_error, float(np.max(np.abs(local.astype(float)+origin-world))))
            faces = data['faces'].copy()
            if np.linalg.det(matrix) < 0:
                faces = faces[:, [0, 2, 1]]
            points.append(local)
            triangles.append(faces+offset)
            offset += len(local)
        vertices = np.concatenate(points)
        faces = np.concatenate(triangles)
        if len(faces) > 500000:
            raise ValueError('spatial collision cell exceeds triangle budget')
        file = out/'meshes'/(identity+'.fbx')
        proof = write_fbx(file, identity, vertices, faces)
        meshes.append(dict(id=identity, file='meshes/'+file.name,
                           asset=PREFIX+identity+'.'+identity, sha256=sha(file),
                           mode='triangle_bvh', vertices=len(vertices), triangles=len(faces),
                           bounds_m=bounds(vertices),
                           geometry_sha256=hashlib.sha256(vertices.astype('<f4').tobytes()+faces.astype('<i4').tobytes()).hexdigest(),
                           source_models=sorted({x['model'] for x in entries})))
        binding = out/'source-bindings'/(identity+'.json')
        save(binding, entries)
        add_cell('baked_'+cell_name(*key), identity,
                 [[*origin, 0, 0, 0, 1, 1, 1, 1]], len(entries),
                 bounds(vertices.astype(float)+origin), binding)
        records.append(dict(cell=identity, generated_fbx=proof))
    result = dict(schema='rm_generated_static_collision_v1', map_id=manifest['id'],
                  source_sha256=manifest['source_sha256'],
                  scene_source_snapshot_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                  source_affine_map_sha256=sha(affine_file), recipe_sha256=recipe_hash,
                  units='m', coordinate_system=manifest['coordinate_system'],
                  cell_size_m=args.cell_size_m, source_instances=sum(counts.values()),
                  source_counts=counts, meshes=meshes, cells=cells)
    save(out/'manifest.json', result)
    save(evidence/'geometry-roundtrips.json', records)
    save(evidence/'summary.json', dict(
        schema='wt_generated_collision_preparation_v1', status='passed',
        scope='offline source geometry and transform preservation; native gates separate',
        manifest_file=str(out/'manifest.json'), manifest_sha256=sha(out/'manifest.json'),
        recipe_sha256=recipe_hash, script_sha256=sha(Path(__file__)),
        source_sha256=manifest['source_sha256'], source_instances=sum(counts.values()),
        rigid_source_instances=sum(len(x) for x in source_rows.values()),
        affine_source_instances=sum(len(x) for x in affine_by_name.values()),
        source_models=len(models), unique_master_geometry=len(geometry_ids),
        mesh_assets=len(meshes), collision_cells=len(cells),
        physical_instance_slots=sum(c['instances'] for c in cells),
        shared_master_triangles=sum(m['triangles'] for m in meshes if not m['id'].startswith('gc_affine_')),
        affine_cell_triangles=sum(m['triangles'] for m in meshes if m['id'].startswith('gc_affine_')),
        source_counts=counts, max_affine_local_float32_error_m=max_affine_error,
        fbx_geometry_roundtrips=len(records),
        geometry_report_sha256=sha(evidence/'geometry-roundtrips.json'),
        scene_source_unchanged=scene_file.read_bytes() == manifest_bytes,
        generated_assets_local_only=True))
    print('WT_GENERATED_COLLISION_PREPARED '+json.dumps(read(evidence/'summary.json')), flush=True)


if __name__ == '__main__':
    main()
