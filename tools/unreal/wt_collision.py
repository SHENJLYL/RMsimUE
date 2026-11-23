"""Strict local contract for derived Flanders sleeper/curb/steel-rail collision.

The source map, visual assets and original collider counts are separate from
this explicit renderer-to-physics adaptation. Dense payloads remain local.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import struct

SCHEMA = 'rm_generated_static_collision_v1'
MAP_ID = 'wt_vlaanderen'
SOURCE_SHA256 = '427c9b06b62af0635fa08f2d6238211b9850caf7717a0773f186e1835be63b8f'
PREFIX = '/Game/RMSim/Maps/wt_vlaanderen/GeneratedCollision/Meshes/'
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

# The 34 SCN steel-rail objects were absent from the source SCol. Keep their
# identities separate from instanced prop models and pin the audited extraction.
STATIC_RAIL_SOURCE = 'world_static_rails'
STATIC_RAIL_MATERIAL_INDEX = 3
STATIC_RAIL_MATERIAL = 'railroad_rails_tile_a'
STATIC_RAIL_MATERIAL_ID = 'WT_5f41d867eef187b5ac52'
STATIC_RAIL_SCENE_SHA256 = 'ffea6ea2d56a1f3c7e98d7d1dae82e194ed1951026aa343b3e00ccef0e60d730'
STATIC_RAIL_VISUAL_SHA256 = '07344e0ea99562383042088cb40887143744ab7847ab7d4aa68f07ea8e5772c7'
STATIC_RAIL_VISUAL_RMI_SHA256 = '0b3b28b52a2805bd5ab4e8d9da693a2e71d37f10a09aa6684f6839d36875de9a'
STATIC_RAILS = {
    'static_0019.npz': (2068, '8eb01630b1489f34968b2d64e5ac73194af03739edccf995ecfe048ffa268f26'),
    'static_0020.npz': (2068, '026cfcd07d9a84ecb0394a88c6cb52512af61f8f31d0494121027dbf4d7bace5'),
    'static_0021.npz': (1166, '2ee6a591d75c68d52dc2d69edf4b58571639ee5979a46868f70fae264d454c46'),
    'static_0022.npz': (1166, '9fdb463d675a0b96cf91ad9a678abc9fac9c47c4b00912049119799a88ed2b1c'),
    'static_0023.npz': (2310, '6a1a0624a661c48d70a23bc488c03ed65cf9e525db08d6b0b905858227725084'),
    'static_0024.npz': (2310, 'c74ce370d84cb9e8ebd17fef2e76cec1f4d1c4f8ceb8a49fa902d49f0ec69f3c'),
    'static_0025.npz': (1320, '996b6ecb252e52f9b9fb6b099b998d8dc45a044698dfac951dfc72d391ca2f3b'),
    'static_0026.npz': (1320, '0cc9c8689a274d2d80538f56c86f17aabae0c4d574320ac671a5d5ff1ec5521b'),
    'static_0027.npz': (550, '7255b46dc79f49e40443dc026ce0a592089772cbae6c6213b243581852be398d'),
    'static_0028.npz': (550, '967cb969b72e6630f433c4c3d00f517f7b4b05c4c156be23afcc2d8e0036fcc9'),
    'static_0029.npz': (2002, '36666ff8187f972788ddd968217799f79c0b42da40e016a41e4e2bc5b13bdae1'),
    'static_0030.npz': (2002, '7c37eb3c186b89980e1e689e139f2006e029129b84697fcc257e2d5dd2291c1e'),
    'static_0031.npz': (3982, '6c8ae172c49dd2b39d4eec5f834c22ffeef494a7682ac89ab7338d847e27cb4a'),
    'static_0032.npz': (3982, 'ff1d2719830aad82ea3f84befc5a4791b877d3dfce8dc37c2ca586ca516e8cce'),
    'static_0033.npz': (6656, '468fadfc16eba56f2633c13705844d7b3eb862b43f96b5a8986f052f80c73727'),
    'static_0034.npz': (6656, 'dd9b6bb16d4666bc4b3eb04eae0196c796b7073f6a34a2b12d74494f85b6ecbe'),
    'static_0038.npz': (5918, 'fee3f0e14b3ec72895697fef21f24eb38f66569df602d6f840b26c6b8524931a'),
    'static_0039.npz': (5918, '85741f2dbb66d634680f3182796965cccc2a3e95d42c97491d902fc9ad3f23d3'),
    'static_0040.npz': (10934, '7545fec738b9fba030fe6fa41ca01c411cb8943d45b6ede5e770050916a509f7'),
    'static_0041.npz': (10934, '10272dbb1c19d77a51f9c731b5b3565d7f99a9bca6c01da08c60ac6e8ea6af03'),
    'static_0042.npz': (6798, '9298b03b0ba0d5f55de39262e867d3dc68be7e503201baa4fa3a0060b4d565dc'),
    'static_0043.npz': (6798, 'ae168f240d0accbc2d157251dce4976a5cf80de843df7c30279fd0744587a1f1'),
    'static_0044.npz': (1166, 'aca93f925287c04d61b3b2c6f856c80a3fddf6d907047a8d5b8d6cec0d3e7cdf'),
    'static_0045.npz': (1166, '205ac403368284b6de8a103b2e7f96cc7b00f0e6108f040710b30d09b284286e'),
    'static_0048.npz': (10922, '68ac65aa6ce2b01013db814f94a49ad38bdb5ba4c993739b38d467276d17a1fa'),
    'static_0049.npz': (10922, '3d1b114520d9fc5e6632398e71bcf3a8cd3199d88cff011e63282c7b05aa45d5'),
    'static_0052.npz': (6656, 'b9f0d2979479fe4ab07fb855fa5dab83acc382840a7d2188d713e7eafc71763c'),
    'static_0053.npz': (6656, '109b800a91c3917472afba3b18b441b6169a2e40a5b2849543fe8345dd1d427d'),
    'static_0054.npz': (6798, 'ce497417baf5fcfcdfb75842dc7423d79b03af60869832f374438f03d8769301'),
    'static_0055.npz': (6798, '2feaa005ee471cdb50cd9f0532a83952ef13c9d003490fbb8ab7533c18cf5d70'),
    'static_0056.npz': (5461, '44b78f54a74d1cf9298078596da4d16d3d3096dc4e5f15c43d180ade1ccfa38a'),
    'static_0057.npz': (5461, '6591c2bb3f3276ca65a203ac340e8913c7cd885e79908e1f058a96d39048fcb9'),
    'static_0058.npz': (5461, '759cd1ea5d51afdcb2803f0e70d5983d7a8fea9fa2f384ce07f6ed1165b73f9f'),
    'static_0059.npz': (5461, '12c390175c5a4d5ae54e4abd257f13ed32df0dad20947ce4f40448a3b7856c4e'),
}
PROP_COUNTS = COUNTS
STATIC_RAIL_COUNT = len(STATIC_RAILS)
SOURCE_COUNTS = dict(COUNTS, **{STATIC_RAIL_SOURCE: STATIC_RAIL_COUNT})
STATIC_RAIL_RECIPE = dict(
    source_scene_sha256=STATIC_RAIL_SCENE_SHA256,
    source_material_index=STATIC_RAIL_MATERIAL_INDEX,
    source_material_name=STATIC_RAIL_MATERIAL,
    source_files=sorted(STATIC_RAILS), source_objects=len(STATIC_RAILS),
    source_triangles=sum(value[0] for value in STATIC_RAILS.values()),
    triangle_policy='every selected source triangle, one static BVH per SCN object',
    localization='float32 centroid translation in RMI, unchanged world placement',
    fbx_axis_policy='exact cardinal node rotation for dedicated steel-rail collision only',
    fbx_node_rotation_degrees=[0., 0., -90.], fbx_unit_scale_factor_cm=100.)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(path, algorithm='sha256'):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def save(path, value):
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temporary.replace(path)


def local(root, value):
    path = Path(value)
    if path.is_absolute() or '..' in path.parts or ':' in str(value):
        raise ValueError('supplement path must be local and relative')
    result = (root/path).resolve(strict=True)
    if not result.is_relative_to(root) or not result.is_file():
        raise ValueError('supplement file escapes its root')
    return result


def finite_vector(value):
    return (isinstance(value, list) and len(value) == 3
            and all(type(x) in (int, float) and math.isfinite(x) and abs(x) <= 100000
                    for x in value))


def valid_bounds(value):
    return (isinstance(value, dict) and finite_vector(value.get('lo'))
            and finite_vector(value.get('hi'))
            and all(a <= b for a, b in zip(value['lo'], value['hi'])))


def rmi_rows(path):
    raw = path.read_bytes()
    if len(raw) < 8 or raw[:4] != b'RMI1':
        raise ValueError('invalid RMI magic')
    count = struct.unpack_from('<I', raw, 4)[0]
    if not 0 < count <= 100000 or len(raw) != 8+40*count:
        raise ValueError('invalid RMI length/count')
    rows = list(struct.iter_unpack('<10f', raw[8:]))
    for row in rows:
        if (not all(math.isfinite(v) for v in row)
                or abs(sum(q*q for q in row[3:7])-1) > .001
                or not all(0 < s <= 1000 for s in row[7:10])):
            raise ValueError('invalid source transform')
    return rows


def load_contract(source_file, generated_root=None):
    source_file = Path(source_file).resolve(strict=True)
    package = source_file.parent
    source = read(source_file)
    if (source.get('schema') != 'rm_scene_map_source_v1' or source.get('id') != MAP_ID
            or source.get('source_sha256') != SOURCE_SHA256):
        raise ValueError('generated collision is bound to the verified Flanders level')
    root = Path(generated_root or package/'generated-collision').resolve(strict=True)
    if (root.parent != package or not (
            root.name == 'generated-collision' or
            root.name.startswith('generated-collision-stage-'))):
        raise ValueError('generated collision must be a dedicated package supplement/stage')
    path = root/'manifest.json'
    manifest = read(path)
    if (manifest.get('schema') != SCHEMA or manifest.get('map_id') != MAP_ID
            or manifest.get('source_sha256') != SOURCE_SHA256
            or manifest.get('units') != 'm'
            or manifest.get('coordinate_system') != 'ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP'
            or manifest.get('source_counts') != SOURCE_COUNTS
            or manifest.get('source_instances') != sum(SOURCE_COUNTS.values())):
        raise ValueError('supplement identity/coverage differs')
    recipe = read(root/'recipe.json')
    recipe_hash = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if (recipe_hash != manifest.get('recipe_sha256') or recipe.get('models') != COUNTS
            or recipe.get('version') != 2 or recipe.get('static_world') != STATIC_RAIL_RECIPE):
        raise ValueError('recipe hash or coverage differs')
    if digest(package/'affine-bake-map.json') != manifest['source_affine_map_sha256']:
        raise ValueError('source affine transforms changed')
    original = {m.get('source'): m for m in source['meshes'] if m.get('source') in COUNTS}
    for name, count in COUNTS.items():
        binding = read(root/'source-bindings'/(name+'.json'))
        if binding['model'] != name or binding['source_instances'] != count:
            raise ValueError('original source binding differs')
        if name in original:
            spec = original[name]
            if (binding['original_visual_fbx_sha256'] != spec['sha256']
                    or digest(local(package, spec['file'])) != spec['sha256']
                    or digest(local(package, spec['instances_file'])) != binding['original_rmi_sha256']):
                raise ValueError('original visual geometry/instances changed: '+name)
    meshes = {}
    for spec in manifest['meshes']:
        identity = spec['id']
        if (not re.fullmatch(r'gc_[A-Za-z0-9_]+', identity) or identity in meshes
                or spec['asset'] != PREFIX+identity+'.'+identity
                or spec['mode'] not in ('convex', 'triangle_bvh')
                or type(spec['triangles']) is not int or not 0 < spec['triangles'] <= 500000
                or not valid_bounds(spec['bounds_m'])
                or digest(local(root, spec['file'])) != spec['sha256']):
            raise ValueError('invalid generated mesh: '+str(identity))
        if spec['mode'] == 'convex':
            corners = spec.get('convex_vertices_m', [])
            if len(corners) != 8 or not all(finite_vector(p) for p in corners):
                raise ValueError('convex proxy must use eight source corners')
        meshes[identity] = spec
    if not 0 < len(meshes) <= 4096:
        raise ValueError('invalid generated mesh count')
    ids, physical, sources = set(), 0, 0
    for cell in manifest['cells']:
        if (cell['id'] in ids or cell['mesh_id'] not in meshes
                or not valid_bounds(cell['bounds_m'])
                or type(cell['source_instances']) is not int or cell['source_instances'] < 1):
            raise ValueError('invalid generated spatial cell')
        file = local(root, cell['instances_file'])
        if digest(file) != cell['sha256'] or digest(file, 'sha1') != cell['sha1']:
            raise ValueError('generated RMI hash differs')
        rows = rmi_rows(file)
        if len(rows) != cell['instances']:
            raise ValueError('generated RMI count differs')
        if 'source_indices_file' in cell:
            if digest(local(root, cell['source_indices_file'])) != cell['source_indices_sha256']:
                raise ValueError('source row/affine binding differs')
        ids.add(cell['id'])
        physical += len(rows)
        sources += cell['source_instances']
    if not 0 < len(ids) <= 16384 or sources != sum(SOURCE_COUNTS.values()):
        raise ValueError('generated cell source coverage differs')
    rail_sources = validate_static_rail_bindings(package, root, source, manifest, meshes)
    return root, manifest, dict(
        manifest_sha256=digest(path), manifest_sha1=digest(path, 'sha1'),
        recipe_sha256=recipe_hash, source_sha256=SOURCE_SHA256,
        source_instances=sources, physical_instance_slots=physical,
        mesh_assets=len(meshes), spatial_cells=len(ids),
        scene_source_current_sha256=digest(source_file),
        scene_source_preparation_snapshot_sha256=manifest['scene_source_snapshot_sha256'],
        source_counts=SOURCE_COUNTS, static_rail_sources=rail_sources)


def validate_static_rail_bindings(package, root, source, manifest, meshes):
    """Validate original SCN/NPZ, unchanged visual, exact export and RMI placement."""
    index_spec = manifest.get('static_source_bindings', {})
    index_file = local(root, index_spec.get('file', ''))
    if digest(index_file) != index_spec.get('sha256'):
        raise ValueError('static rail source-binding index differs')
    index = read(index_file)
    original_root = Path(index['original_source_root']).resolve(strict=True)
    scene_file = local(original_root, 'static-02/scene.json')
    if (digest(scene_file) != STATIC_RAIL_SCENE_SHA256
            or index.get('source_scene_sha256') != STATIC_RAIL_SCENE_SHA256
            or index.get('source_objects') != len(STATIC_RAILS)
            or index.get('source_triangles') != STATIC_RAIL_RECIPE['source_triangles']):
        raise ValueError('original static SCN identity/coverage differs')
    scene = read(scene_file)
    if (scene['sha256'] != SOURCE_SHA256
            or scene['materials'][STATIC_RAIL_MATERIAL_INDEX]['name'] != STATIC_RAIL_MATERIAL):
        raise ValueError('steel-rail material/source level differs')
    visuals = [m for m in source['meshes'] if m['id'] == 'world_static_000']
    if len(visuals) != 1:
        raise ValueError('original static visual identity differs')
    visual = visuals[0]
    visual_proof = index.get('original_visual_geometry', {})
    if (visual['sha256'] != STATIC_RAIL_VISUAL_SHA256
            or index.get('original_visual_fbx_sha256') != STATIC_RAIL_VISUAL_SHA256
            or index.get('original_visual_fbx_file') != visual['file']
            or digest(local(package, visual['file'])) != STATIC_RAIL_VISUAL_SHA256
            or STATIC_RAIL_MATERIAL_ID not in visual['material_slots']
            or visual.get('instance_count') != 1
            or index.get('original_visual_rmi_file') != visual['instances_file']
            or index.get('original_visual_rmi_sha256') != STATIC_RAIL_VISUAL_RMI_SHA256
            or digest(local(package, visual['instances_file'])) != STATIC_RAIL_VISUAL_RMI_SHA256
            or rmi_rows(local(package, visual['instances_file'])) !=
                [(0., 0., 0., 0., 0., 0., 1., 1., 1., 1.)]
            or visual_proof.get('exact_float32_source_faces') is not True
            or visual_proof.get('triangles') != visual['triangles']
            or visual_proof.get('vertices') != visual['vertices']
            or visual_proof.get('source_original_vertices') !=
                sum(o['vertices'] for o in scene['objects'])
            or visual_proof.get('duplicate_source_draw_faces_preserved', -1)*3 !=
                visual['vertices']-sum(o['vertices'] for o in scene['objects'])
            or visual_proof.get('exact_material_face_assignment') is not True
            or visual_proof.get('material_slots') != visual['material_slots']
            or visual_proof.get('material_face_counts', [])[visual['material_slots'].index(
                STATIC_RAIL_MATERIAL_ID)] != STATIC_RAIL_RECIPE['source_triangles']):
        raise ValueError('original world visual/source geometry differs')
    original_objects = {o['file']: o for o in scene['objects']}
    cells = {c['id']: c for c in manifest['cells']}
    seen, total = set(), 0
    for entry in index['bindings']:
        binding_file = local(root, entry['file'])
        if digest(binding_file) != entry['sha256']:
            raise ValueError('static rail object binding differs')
        binding = read(binding_file)
        name = binding['source_npz_file']
        if name not in STATIC_RAILS or name in seen:
            raise ValueError('unexpected or repeated static rail object')
        count, source_hash = STATIC_RAILS[name]
        source_object = original_objects[name]
        identity = 'gc_static_rail_'+str(source_object['index']).zfill(4)
        cell_id = 'static_rail_'+str(source_object['index']).zfill(4)
        spec, cell = meshes.get(identity, {}), cells.get(cell_id, {})
        if (digest(local(original_root, 'static-02/'+name)) != source_hash
                or binding.get('source_npz_sha256') != source_hash
                or binding.get('source_scene_sha256') != STATIC_RAIL_SCENE_SHA256
                or binding.get('source_object_name') != source_object['name']
                or binding.get('source_object_index') != source_object['index']
                or binding.get('source_material_index') != STATIC_RAIL_MATERIAL_INDEX
                or binding.get('source_material_name') != STATIC_RAIL_MATERIAL
                or binding.get('source_triangles') != count
                or binding.get('selected_triangles') != count
                or binding.get('master_id') != identity
                or binding.get('source_instances') != 1
                or binding.get('original_visual_fbx_sha256') != STATIC_RAIL_VISUAL_SHA256
                or not finite_vector(binding.get('origin_ros_m'))
                or not 0 <= binding.get('max_world_roundtrip_error_m', -1) <= .0001):
            raise ValueError('original static rail binding differs: '+name)
        proof = binding.get('generated_fbx', {})
        axis_proof = proof.get('native_axis_transform', {})
        if (axis_proof.get('policy') != STATIC_RAIL_RECIPE['fbx_axis_policy']
                or axis_proof.get('rotation_degrees') != [0., 0., -90.]
                or axis_proof.get('unit_scale_factor_cm') != 100.
                or axis_proof.get('translation') != [0., 0., 0.]
                or axis_proof.get('scale') != [1., 1., 1.]
                or axis_proof.get('all_other_fbx_elements_unchanged') is not True
                or axis_proof.get('axes') != dict(
                    UpAxis=2, UpAxisSign=1, FrontAxis=0, FrontAxisSign=-1,
                    CoordAxis=1, CoordAxisSign=-1)):
            raise ValueError('static rail FBX cardinal-axis/unit contract differs')
        if (spec.get('source_models') != [STATIC_RAIL_SOURCE]
                or spec.get('source_objects') != [name]
                or spec.get('mode') != 'triangle_bvh'
                or spec.get('triangles') != count
                or spec.get('geometry_sha256') != binding.get('geometry_sha256')
                or proof.get('exact_float32_source_faces') is not True
                or proof.get('triangles') != count
                or proof.get('vertices') != spec.get('vertices')
                or cell.get('mesh_id') != identity
                or cell.get('instances') != 1 or cell.get('source_instances') != 1
                or cell.get('source_indices_file') != entry['file']
                or cell.get('source_indices_sha256') != entry['sha256']):
            raise ValueError('static rail exact-triangle/instance contract differs: '+name)
        faces_file = local(root, binding['source_face_indices_file'])
        if digest(faces_file) != binding['source_face_indices_sha256']:
            raise ValueError('static rail selected-face provenance differs')
        selected = list(struct.iter_unpack('<I', faces_file.read_bytes()))
        # All triangles in the pinned 34 objects use the audited rail material.
        if [value[0] for value in selected] != list(range(count)):
            raise ValueError('static rail faces were omitted, repeated or replaced')
        rows = rmi_rows(local(root, cell['instances_file']))
        if list(rows[0]) != binding['origin_ros_m']+[0., 0., 0., 1., 1., 1., 1.]:
            raise ValueError('static rail RMI world placement differs')
        expected = {key: [spec['bounds_m'][key][i]+rows[0][i] for i in range(3)]
                    for key in ('lo', 'hi')}
        if any(abs(expected[key][i]-cell['bounds_m'][key][i]) > 1e-8
               for key in expected for i in range(3)):
            raise ValueError('static rail streaming bounds differ from localized geometry')
        seen.add(name)
        total += count
    if seen != set(STATIC_RAILS) or total != STATIC_RAIL_RECIPE['source_triangles']:
        raise ValueError('static rail source coverage differs')
    rail_meshes = {key for key, value in meshes.items()
                   if STATIC_RAIL_SOURCE in value.get('source_models', [])}
    if rail_meshes != {'gc_static_rail_'+name[7:11] for name in STATIC_RAILS}:
        raise ValueError('static rail mesh multiplicity differs')
    return dict(objects=len(seen), triangles=total, material=STATIC_RAIL_MATERIAL,
                source_scene_sha256=STATIC_RAIL_SCENE_SHA256)


def asset_file(content, asset):
    if not asset.startswith(PREFIX):
        raise ValueError('generated mesh is outside its dedicated folder')
    result = (content/(asset.split('.')[0][len('/Game/'):]+'.uasset')).resolve()
    if not result.is_relative_to(content):
        raise ValueError('generated asset escaped project content')
    return result


def bounds_record(mesh, spec):
    box = mesh.get_bounding_box()
    actual = dict(lo=[box.min.x, box.min.y, box.min.z],
                  hi=[box.max.x, box.max.y, box.max.z])
    expected = {
        'lo': [spec['bounds_m']['lo'][0]*100, -spec['bounds_m']['hi'][1]*100, spec['bounds_m']['lo'][2]*100],
        'hi': [spec['bounds_m']['hi'][0]*100, -spec['bounds_m']['lo'][1]*100, spec['bounds_m']['hi'][2]*100]}
    error = max(abs(actual[k][i]-expected[k][i]) for k in actual for i in range(3))
    if error > .01:
        raise ValueError('generated native bounds differ by '+str(error)+' cm: '+spec['id'])
    return dict(actual_ue_cm=actual, source_expected_ue_cm=expected,
                max_error_cm=error, tolerance_cm=.01)


def check_native(native, spec, manifest):
    if (native.get('valid') is not True or native.get('asset') != spec['asset']
            or native.get('triangles') != spec['triangles']
            or native.get('mode') != spec['mode']
            or native.get('source_fbx_sha256') != spec['sha256']
            or native.get('recipe_sha256') != manifest['recipe_sha256']
            or native.get('cooked') is not True):
        raise ValueError('saved native collision contract differs: '+spec['id'])
    wanted = (0, 1, 1) if spec['mode'] == 'convex' else (1, 0, 0)
    actual = tuple(native.get(k) for k in ('triangle_bvhs', 'convex_hulls', 'simple_shapes'))
    if actual != wanted:
        raise ValueError('saved collision must contain one shared shape: '+spec['id'])