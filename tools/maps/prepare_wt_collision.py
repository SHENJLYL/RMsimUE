"""Blender: derive Flanders sleeper/curb/steel-rail collision from source geometry.

Original extraction, scene-source, visuals, and World remain read-only. Source
triangles share a static BVH; four convex curb variants share an eight-point
convex. Full affine transforms are baked into small, local-coordinate cells.
Steel rails retain every source triangle in one centroid-local BVH per SCN
object. All generated meshes/instance streams stay in the local ignored package.
Use a fresh generated-collision-stage-* sibling for an existing supplement;
validate the complete stage before the caller swaps it into the canonical path.
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
from io_scene_fbx import parse_fbx, encode_bin

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_wt_decal_opacity import encode_copy as encode_fbx_tree, fingerprint as fbx_fingerprint

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'unreal'))
from wt_collision import (
    STATIC_RAILS, STATIC_RAIL_SOURCE, STATIC_RAIL_MATERIAL_INDEX,
    STATIC_RAIL_MATERIAL, STATIC_RAIL_MATERIAL_ID, STATIC_RAIL_SCENE_SHA256,
    STATIC_RAIL_VISUAL_SHA256, STATIC_RAIL_VISUAL_RMI_SHA256, STATIC_RAIL_RECIPE,
    SOURCE_COUNTS, load_contract, rmi_rows)

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


def verify_fbx(path, vertices, faces, material_ids=None, material_slots=None):
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
    material_proof, wanted_material_faces = {}, {}
    actual_materials = None
    if material_ids is not None:
        objects = child(root, b'Objects')
        models = [o for o in objects.elems if o.id == b'Model' and o.props[2] == b'Mesh']
        if len(models) != 1 or material_slots is None:
            raise ValueError('FBX material proof requires one mesh and explicit slots')
        materials = {o.props[0]: o.props[1].split(bytes([0]))[0].decode()
                     for o in objects.elems if o.id == b'Material'}
        slots = [materials[c.props[1]] for c in child(root, b'Connections').elems
                 if c.props[0] == b'OO' and c.props[2] == models[0].props[0]
                 and c.props[1] in materials]
        layer = child(geometry, b'LayerElementMaterial')
        if (slots != material_slots
                or child(layer, b'MappingInformationType').props[0] != b'ByPolygon'
                or child(layer, b'ReferenceInformationType').props[0] != b'IndexToDirect'):
            raise ValueError('original visual material connections/mapping differ')
        actual_materials = np.asarray(child(layer, b'Materials').props[0], dtype=np.int32)
        material_ids = np.asarray(material_ids, dtype=np.int32)
        if (actual_materials.shape != (len(faces),) or material_ids.shape != (len(faces),)
                or np.any(actual_materials < 0) or np.any(actual_materials >= len(slots))):
            raise ValueError('original visual material array differs')
        wanted_material_faces = {
            index: canonical_faces(vertices.astype(np.float64)[faces[material_ids == index]])
            for index in range(len(slots))}
        material_proof = dict(
            exact_material_face_assignment=True, material_slots=slots,
            material_face_counts=[int(np.sum(material_ids == index))
                                  for index in range(len(slots))])
    # Blender preserves identity geometry axes for these files; try it first,
    # retaining the complete exact-axis fallback used by earlier preparations.
    identity = ((0, 1, 2), (1., 1., 1.))
    axes_signs = [identity] + [
        (axes, signs) for axes in itertools.permutations(range(3))
        for signs in itertools.product((-1., 1.), repeat=3) if (axes, signs) != identity]
    for axes, signs in axes_signs:
        mapped = actual[:, axes] * signs
        if not np.array_equal(canonical_faces(mapped[triangles]), wanted):
            continue
        if any(not np.array_equal(
                canonical_faces(mapped[triangles[actual_materials == index]]), expected)
                for index, expected in wanted_material_faces.items()):
            continue
        return dict(vertices=len(actual), triangles=len(triangles),
                    exact_float32_source_faces=True,
                    binary_fbx_version=int(version), axis_permutation=list(axes),
                    axis_signs=list(signs), **material_proof)
    raise ValueError('binary FBX geometry does not match every source triangle: ' + str(path))


def normalize_static_rail_fbx_axis(path):
    """Make the dedicated rail export's cardinal node transform exact.

    Blender's float rotation export is -90.000009334... degrees. That residual
    becomes measurable over a kilometre-long object. Normalize only this new
    collision FBX node, then prove every other FBX element was preserved.
    """
    root, version = parse_fbx.parse(str(path))
    objects = child(root, b'Objects')
    models = [x for x in objects.elems if x.id == b'Model' and x.props[2] == b'Mesh']
    if len(models) != 1:
        raise ValueError('rail axis normalization requires one mesh model')
    properties = child(models[0], b'Properties70')
    values = {x.props[0]: list(x.props[4:]) for x in properties.elems}
    old_rotation = values.get(b'Lcl Rotation')
    if (old_rotation is None or len(old_rotation) != 3
            or max(abs(a-b) for a, b in zip(old_rotation, (0., 0., -90.))) > .0001):
        raise ValueError('unexpected rail FBX rotation; refusing arbitrary snapping')
    for key, default in (
            (b'Lcl Translation', [0., 0., 0.]), (b'Lcl Scaling', [1., 1., 1.]),
            (b'PreRotation', [0., 0., 0.]), (b'PostRotation', [0., 0., 0.]),
            (b'RotationOffset', [0., 0., 0.]), (b'RotationPivot', [0., 0., 0.]),
            (b'ScalingOffset', [0., 0., 0.]), (b'ScalingPivot', [0., 0., 0.]),
            (b'GeometricTranslation', [0., 0., 0.]),
            (b'GeometricRotation', [0., 0., 0.]),
            (b'GeometricScaling', [1., 1., 1.])):
        if values.get(key, default) != default:
            raise ValueError('unexpected additional rail FBX transform: '+repr(key))
    globals_ = {x.props[0]: list(x.props[4:]) for x in
                child(child(root, b'GlobalSettings'), b'Properties70').elems}
    expected_globals = {
        b'UpAxis': [2], b'UpAxisSign': [1], b'FrontAxis': [0], b'FrontAxisSign': [-1],
        b'CoordAxis': [1], b'CoordAxisSign': [-1], b'UnitScaleFactor': [100.],
        b'OriginalUnitScaleFactor': [100.]}
    if any(globals_.get(key) != value for key, value in expected_globals.items()):
        raise ValueError('unexpected rail FBX axes or units')
    rotations = [x for x in properties.elems if x.props[0] == b'Lcl Rotation']
    if len(rotations) != 1:
        raise ValueError('unexpected rail FBX rotation property multiplicity')
    rotations[0].props[4:7] = [0., 0., -90.]
    expected_fingerprint = fbx_fingerprint(root)
    temporary = path.with_name(path.stem+'.axis-staged.fbx')
    if temporary.exists():
        raise ValueError('axis normalization requires a fresh temporary output')
    encode_bin.write(str(temporary), encode_fbx_tree(root), version)
    checked, checked_version = parse_fbx.parse(str(temporary))
    if checked_version != version or fbx_fingerprint(checked) != expected_fingerprint:
        raise ValueError('rail axis normalization changed another FBX element')
    temporary.replace(path)
    return dict(
        policy='exact cardinal node rotation for dedicated steel-rail collision only',
        original_rotation_degrees=old_rotation, rotation_degrees=[0., 0., -90.],
        translation=[0., 0., 0.], scale=[1., 1., 1.],
        unit_scale_factor_cm=100.,
        axes={key.decode(): value[0] for key, value in expected_globals.items()
              if b'Axis' in key},
        all_other_fbx_elements_unchanged=True,
        preserved_elements_sha256=expected_fingerprint,
        binary_fbx_version=int(version), parser_sha256=sha(Path(parse_fbx.__file__)),
        writer_sha256=sha(Path(encode_bin.__file__)))


def write_fbx(path, name, vertices, faces, exact_rail_axis=False):
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
    axis_proof = normalize_static_rail_fbx_axis(path) if exact_rail_axis else None
    proof = verify_fbx(path, vertices, faces)
    if axis_proof:
        proof['native_axis_transform'] = axis_proof
    return proof


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


def static_rail_sources(source, package, manifest):
    """Select only the pinned SCN rail material and prove the original visual."""
    scene_file = source/'static-02/scene.json'
    if sha(scene_file) != STATIC_RAIL_SCENE_SHA256:
        raise ValueError('audited source static scene changed')
    scene = read(scene_file)
    material = scene['materials'][STATIC_RAIL_MATERIAL_INDEX]
    if (scene['sha256'] != manifest['source_sha256']
            or material['name'] != STATIC_RAIL_MATERIAL
            or material['shader'] != 'simple_aces'):
        raise ValueError('steel-rail source material identity differs')
    visuals = [m for m in manifest['meshes'] if m['id'] == 'world_static_000']
    if len(visuals) != 1:
        raise ValueError('steel-rail original world visual is missing')
    visual = visuals[0]
    visual_file = package/visual['file']
    if (visual['sha256'] != STATIC_RAIL_VISUAL_SHA256
            or sha(visual_file) != STATIC_RAIL_VISUAL_SHA256
            or STATIC_RAIL_MATERIAL_ID not in visual['material_slots']
            or visual.get('instance_count') != 1
            or sha(package/visual['instances_file']) != STATIC_RAIL_VISUAL_RMI_SHA256
            or rmi_rows(package/visual['instances_file']) !=
                [(0., 0., 0., 0., 0., 0., 1., 1., 1., 1.)]):
        raise ValueError('steel-rail original visual identity differs')
    source_material_ids = []
    for material in scene['materials']:
        identity = {key: material[key] for key in ('shader', 'parameters', 'texture_slots')}
        source_material_ids.append('WT_'+hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:20])
    slot_mapping = {index: visual['material_slots'].index(identity)
                    for index, identity in enumerate(source_material_ids)
                    if identity in visual['material_slots']}
    groups, all_points, all_faces, all_materials, offset = [], [], [], [], 0
    selected_files = set()
    for item in scene['objects']:
        file = source/'static-02'/item['file']
        with np.load(file) as archive:
            vertices = archive['vertices'].astype(np.float64)
            faces = archive['triangles'].astype(np.int32)
            material_ids = archive['material_ids'].astype(np.int32)
        if (vertices.shape != (item['vertices'], 3)
                or faces.shape != (item['triangles'], 3)
                or material_ids.shape != (len(faces),)
                or not np.all(np.isfinite(vertices))
                or np.any(faces < 0) or np.any(faces >= len(vertices))):
            raise ValueError('source SCN arrays differ: '+item['file'])
        world = (vertices @ C.T).astype(np.float32)
        all_points.append(world)
        all_faces.append(faces+offset)
        all_materials.append(np.asarray([slot_mapping[int(index)] for index in material_ids],
                                         dtype=np.int32))
        offset += len(vertices)
        indices = np.flatnonzero(material_ids == STATIC_RAIL_MATERIAL_INDEX)
        if not len(indices):
            continue
        if item['file'] not in STATIC_RAILS or item['file'] in selected_files:
            raise ValueError('unexpected/repeated steel-rail source object')
        expected_count, expected_hash = STATIC_RAILS[item['file']]
        if (len(indices) != expected_count or sha(file) != expected_hash
                or not np.array_equal(indices, np.arange(len(faces)))):
            raise ValueError('steel-rail source triangles/hash differ: '+item['file'])
        selected_faces = faces[indices]
        used, inverse = np.unique(selected_faces.reshape(-1), return_inverse=True)
        compact_world = world[used].astype(np.float64)
        compact_faces = inverse.reshape(-1, 3).astype(np.int32)
        origin = compact_world.mean(axis=0).astype(np.float32).astype(np.float64)
        local = (compact_world-origin).astype(np.float32)
        error = float(np.max(np.abs(local.astype(float)+origin-compact_world)))
        if error > .0001:
            raise ValueError('steel-rail local float32 placement loses source precision')
        area = np.cross(compact_world[compact_faces[:, 1]]-compact_world[compact_faces[:, 0]],
                        compact_world[compact_faces[:, 2]]-compact_world[compact_faces[:, 0]])
        if np.any(np.linalg.norm(area, axis=1) == 0):
            raise ValueError('steel-rail source has a zero-area triangle')
        groups.append(dict(item=item, source_npz_sha256=expected_hash,
                           source_vertices=len(vertices), source_triangles=len(faces),
                           face_indices=indices, vertices=local, faces=compact_faces,
                           origin=origin, max_world_roundtrip_error_m=error,
                           source_bounds_m=bounds(compact_world)))
        selected_files.add(item['file'])
    if selected_files != set(STATIC_RAILS):
        raise ValueError('steel-rail source object coverage differs')
    # The source SCN is merged into one visual FBX. Proving all of its faces
    # also proves that the selected 34 rail submeshes match the unchanged view.
    visual_vertices, visual_faces = np.concatenate(all_points), np.concatenate(all_faces)
    original_vertex_count = len(visual_vertices)
    # Match prepare_wt_scene.Builder.mesh: repeated source draw faces have
    # distinct vertices in the visual FBX so Blender preserves their multiplicity.
    _, first = np.unique(np.sort(visual_faces, axis=1), axis=0, return_index=True)
    repeated = np.setdiff1d(np.arange(len(visual_faces)), first, assume_unique=True)
    if len(repeated):
        duplicated = visual_faces[repeated].ravel()
        visual_vertices = np.concatenate([visual_vertices, visual_vertices[duplicated]])
        visual_faces = visual_faces.copy()
        visual_faces[repeated] = np.arange(
            original_vertex_count, len(visual_vertices)).reshape(-1, 3)
    visual_proof = verify_fbx(visual_file, visual_vertices, visual_faces,
                              np.concatenate(all_materials), visual['material_slots'])
    visual_proof.update(source_original_vertices=original_vertex_count,
                        duplicate_source_draw_faces_preserved=len(repeated))
    index = dict(schema='wt_static_rail_source_bindings_v1',
                 original_source_root=str(source),
                 source_scene_file='static-02/scene.json',
                 source_scene_sha256=STATIC_RAIL_SCENE_SHA256,
                 source_objects=len(groups),
                 source_triangles=sum(len(g['faces']) for g in groups),
                 source_material_index=STATIC_RAIL_MATERIAL_INDEX,
                 source_material_name=STATIC_RAIL_MATERIAL,
                 original_visual_fbx_file=visual['file'],
                 original_visual_fbx_sha256=STATIC_RAIL_VISUAL_SHA256,
                 original_visual_rmi_file=visual['instances_file'],
                 original_visual_rmi_sha256=STATIC_RAIL_VISUAL_RMI_SHA256,
                 original_visual_geometry=visual_proof, bindings=[])
    return groups, index


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
    if (out.parent != package or not (
            out.name == 'generated-collision' or
            out.name.startswith('generated-collision-stage-'))):
        raise ValueError('output must be a fresh dedicated package supplement/stage')
    manifest_bytes = scene_file.read_bytes()
    manifest = json.loads(manifest_bytes.decode('utf-8-sig'))
    if manifest['id'] != 'wt_vlaanderen' or manifest['schema'] != 'rm_scene_map_source_v1':
        raise ValueError('this recipe is for the verified Flanders source')
    rail_groups, rail_index = static_rail_sources(source, package, manifest)
    out.mkdir(parents=True)
    evidence.mkdir(parents=True)
    (out/'meshes').mkdir()
    (out/'instances').mkdir()
    (out/'source-bindings').mkdir()
    recipe = dict(schema='wt_static_collision_recipe_v1', version=2,
                  models=COUNTS, static_world=STATIC_RAIL_RECIPE,
                  cell_size_m=args.cell_size_m,
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

    for group in rail_groups:
        item = group['item']
        suffix = str(item['index']).zfill(4)
        identity = 'gc_static_rail_'+suffix
        vertices, faces, origin = group['vertices'], group['faces'], group['origin']
        file = out/'meshes'/(identity+'.fbx')
        proof = write_fbx(file, identity, vertices, faces, exact_rail_axis=True)
        geometry_hash = hashlib.sha256(
            vertices.astype('<f4').tobytes()+faces.astype('<i4').tobytes()).hexdigest()
        meshes.append(dict(id=identity, file='meshes/'+file.name,
                           asset=PREFIX+identity+'.'+identity, sha256=sha(file),
                           mode='triangle_bvh', vertices=len(vertices), triangles=len(faces),
                           bounds_m=bounds(vertices), geometry_sha256=geometry_hash,
                           source_models=[STATIC_RAIL_SOURCE], source_objects=[item['file']]))
        indices_file = out/'source-bindings'/(identity+'.u32')
        indices_file.write_bytes(np.asarray(group['face_indices'], dtype='<u4').tobytes())
        binding = out/'source-bindings'/(identity+'.json')
        save(binding, dict(
            schema='wt_static_rail_source_object_v1', master_id=identity,
            source_instances=1, source_object_name=item['name'],
            source_object_index=item['index'], source_npz_file=item['file'],
            source_npz_sha256=group['source_npz_sha256'],
            source_scene_sha256=STATIC_RAIL_SCENE_SHA256,
            source_material_index=STATIC_RAIL_MATERIAL_INDEX,
            source_material_name=STATIC_RAIL_MATERIAL,
            source_vertices=group['source_vertices'], source_triangles=group['source_triangles'],
            selected_vertices=len(vertices), selected_triangles=len(faces),
            source_face_indices_file='source-bindings/'+indices_file.name,
            source_face_indices_sha256=sha(indices_file),
            source_bounds_ros_m=group['source_bounds_m'],
            origin_ros_m=origin.tolist(),
            max_world_roundtrip_error_m=group['max_world_roundtrip_error_m'],
            original_visual_fbx_sha256=STATIC_RAIL_VISUAL_SHA256,
            geometry_sha256=geometry_hash, generated_fbx=proof))
        add_cell('static_rail_'+suffix, identity,
                 [[*origin, 0, 0, 0, 1, 1, 1, 1]], 1,
                 bounds(vertices.astype(float)+origin), binding)
        rail_index['bindings'].append(dict(file='source-bindings/'+binding.name,
                                           sha256=sha(binding)))
        records.append(dict(static_object=item['file'], generated_fbx=proof,
                            max_world_roundtrip_error_m=group['max_world_roundtrip_error_m']))
    counts[STATIC_RAIL_SOURCE] = len(rail_groups)
    if counts != SOURCE_COUNTS:
        raise ValueError('complete prop/static source coverage differs')
    static_binding_file = out/'source-bindings/static_rails.json'
    save(static_binding_file, rail_index)
    result = dict(schema='rm_generated_static_collision_v1', map_id=manifest['id'],
                  source_sha256=manifest['source_sha256'],
                  scene_source_snapshot_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                  source_affine_map_sha256=sha(affine_file), recipe_sha256=recipe_hash,
                  units='m', coordinate_system=manifest['coordinate_system'],
                  cell_size_m=args.cell_size_m, source_instances=sum(counts.values()),
                  source_counts=counts, meshes=meshes, cells=cells,
                  static_source_bindings=dict(file='source-bindings/static_rails.json',
                                              sha256=sha(static_binding_file)))
    save(out/'manifest.json', result)
    _, _, complete_contract = load_contract(scene_file, generated_root=out)
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
        static_rail_source_objects=len(rail_groups),
        static_rail_triangles=sum(len(g['faces']) for g in rail_groups),
        max_static_rail_local_float32_error_m=max(g['max_world_roundtrip_error_m'] for g in rail_groups),
        complete_source_contract=complete_contract,
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