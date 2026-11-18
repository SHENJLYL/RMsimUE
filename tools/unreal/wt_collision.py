"""Strict local contract for derived Flanders sleeper/curb collision.

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


def load_contract(source_file):
    source_file = Path(source_file).resolve(strict=True)
    package = source_file.parent
    source = read(source_file)
    if (source.get('schema') != 'rm_scene_map_source_v1' or source.get('id') != MAP_ID
            or source.get('source_sha256') != SOURCE_SHA256):
        raise ValueError('generated collision is bound to the verified Flanders level')
    root = (package/'generated-collision').resolve(strict=True)
    path = root/'manifest.json'
    manifest = read(path)
    if (manifest.get('schema') != SCHEMA or manifest.get('map_id') != MAP_ID
            or manifest.get('source_sha256') != SOURCE_SHA256
            or manifest.get('units') != 'm'
            or manifest.get('coordinate_system') != 'ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP'
            or manifest.get('source_counts') != COUNTS
            or manifest.get('source_instances') != sum(COUNTS.values())):
        raise ValueError('supplement identity/coverage differs')
    recipe = read(root/'recipe.json')
    recipe_hash = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if recipe_hash != manifest.get('recipe_sha256') or recipe.get('models') != COUNTS:
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
    if not 0 < len(ids) <= 16384 or sources != sum(COUNTS.values()):
        raise ValueError('generated cell source coverage differs')
    return root, manifest, dict(
        manifest_sha256=digest(path), manifest_sha1=digest(path, 'sha1'),
        recipe_sha256=recipe_hash, source_sha256=SOURCE_SHA256,
        source_instances=sources, physical_instance_slots=physical,
        mesh_assets=len(meshes), spatial_cells=len(ids),
        scene_source_current_sha256=digest(source_file),
        scene_source_preparation_snapshot_sha256=manifest['scene_source_snapshot_sha256'],
        source_counts=COUNTS)


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
