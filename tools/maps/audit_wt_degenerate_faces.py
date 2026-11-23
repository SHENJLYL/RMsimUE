"""Independently account for strictly coincident source decal faces.

Run with Blender's bundled FBX binary parser and NumPy. No geometry, manifest,
or existing evidence file is written. Example:
  blender --background --factory-startup --python-exit-code 1 --python THIS --
    --scene-source assets/maps/wt_vlaanderen/scene-source.json
    --output-dir artifacts/maps/wt-scene-20261118/decal-proof-01

The source/FBX proof is distinct from the subsequent native UE triangle check.
Only faces with an exactly coincident vertex pair can be accounted for. A
collinear triangle with three distinct positions remains in the native target.
"""
import argparse
from fractions import Fraction
import hashlib
import io
import itertools
import json
import math
from pathlib import Path
import struct
import sys
import traceback

import numpy as np


SCHEMA = 'wt_source_degenerate_face_audit_v1'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def material_id(spec):
    value = {key: spec[key] for key in ('shader', 'parameters', 'texture_slots')}
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    return 'WT_' + hashlib.sha256(encoded).hexdigest()[:20]


def triangle_metrics(points):
    """Use float64 arithmetic on the supplied exact positions; no tolerance."""
    points = np.asarray(points, dtype=np.float64)
    pair = (np.all(points[:, 0] == points[:, 1], axis=1)
            | np.all(points[:, 0] == points[:, 2], axis=1)
            | np.all(points[:, 1] == points[:, 2], axis=1))
    cross = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])
    zero = np.all(cross == 0., axis=1)
    area = np.linalg.norm(cross, axis=1) * .5
    return pair, zero, area


def exact_zero_area(points):
    values = [[Fraction(float(value)) for value in row] for row in points]
    a = [values[1][i]-values[0][i] for i in range(3)]
    b = [values[2][i]-values[0][i] for i in range(3)]
    return all(a[i]*b[j] == a[j]*b[i] for i, j in ((0, 1), (0, 2), (1, 2)))


def canonical_faces(points):
    """Canonical corner order; preserve the original triangle sequence."""
    points = np.asarray(points, dtype=np.float64)
    order = np.lexsort((points[:, :, 2], points[:, :, 1], points[:, :, 0]), axis=1)
    ordered = np.take_along_axis(points, order[:, :, None], axis=1)
    ordered[ordered == 0.] = 0.  # Equal signed zeros have the same byte identity.
    return np.ascontiguousarray(ordered)


def face_keys(points):
    return canonical_faces(points).reshape(-1, 9).view(np.dtype((np.void, 72))).ravel()


def material_face_keys(points, identities):
    geometry = canonical_faces(points).reshape(-1, 9).view(np.uint8).reshape(-1, 72)
    labels = np.asarray(identities, dtype='S23')
    require(all(len(str(value)) == 23 and str(value).startswith('WT_') for value in identities),
            'expected stable WT material identities')
    labels = labels.view(np.uint8).reshape(-1, 23)
    return np.ascontiguousarray(np.concatenate((geometry, labels), axis=1)).view('V95').ravel()


def child(node, identity):
    items = [item for item in node.elems if item.id == identity]
    require(len(items) == 1, 'expected one FBX element ' + repr(identity))
    return items[0]


def text(value):
    return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else str(value)


def properties(node):
    result = {}
    for group in node.elems:
        if group.id not in (b'Properties70', b'Properties60'):
            continue
        for entry in group.elems:
            if entry.id == b'P':
                result[text(entry.props[0])] = [text(v) if isinstance(v, bytes) else v for v in entry.props[4:]]
    return result


def read_fbx(path):
    # Read polygon arrays directly. Importing to a Blender Mesh may validate or
    # remove topology; that would compromise the source accounting evidence.
    try:
        from io_scene_fbx import parse_fbx as parse
    except ImportError as error:
        raise RuntimeError('run this audit with Blender and its io_scene_fbx binary parser') from error
    root, version = parse.parse(str(path))
    objects = child(root, b'Objects')
    geometries = [item for item in objects.elems
                  if item.id == b'Geometry' and item.props[2] == b'Mesh']
    require(len(geometries) == 1, 'audit supports one baked source-world FBX mesh')
    geometry = geometries[0]
    vertices = np.asarray(child(geometry, b'Vertices').props[0], dtype=np.float64).reshape(-1, 3)
    encoded = np.asarray(child(geometry, b'PolygonVertexIndex').props[0], dtype=np.int64)
    ends = np.flatnonzero(encoded < 0)
    require(len(ends) and ends[-1] == len(encoded)-1, 'invalid FBX polygon terminators')
    require(np.all(np.diff(np.r_[-1, ends]) == 3), 'FBX has non-triangular polygons')
    triangles = encoded.copy()
    triangles[triangles < 0] = -triangles[triangles < 0] - 1
    triangles = triangles.reshape(-1, 3)
    require(np.all(triangles >= 0) and np.all(triangles < len(vertices)), 'FBX vertex index outside array')
    settings = properties(child(root, b'GlobalSettings'))
    require('UnitScaleFactor' in settings, 'FBX must declare its centimetre unit scale')
    unit_cm = float(settings['UnitScaleFactor'][0])
    require(np.isfinite(unit_cm) and unit_cm > 0., 'invalid FBX unit scale')
    model_nodes = [item for item in objects.elems if item.id == b'Model']
    require(len(model_nodes) == 1, 'expected one FBX root model')
    model = model_nodes[0]
    templates = {}
    for definition in child(root, b'Definitions').elems:
        if definition.id == b'ObjectType' and definition.props[0] == b'Model':
            for template in definition.elems:
                if template.id == b'PropertyTemplate':
                    templates.update(properties(template))
    effective = dict(templates, **properties(model))
    connections = [item.props for item in child(root, b'Connections').elems if item.id == b'C']
    require([list(row[1:]) for row in connections if row[0] == b'OO' and row[1] == model.props[0]]
            == [[model.props[0], 0]], 'model must have exactly one root parent and no parent transform')
    require([row[2] for row in connections if row[0] == b'OO' and row[1] == geometry.props[0]]
            == [model.props[0]], 'geometry must attach directly to the audited model')
    # Validate all effective FBX Node properties that could move or scale points.
    for key in ('Lcl Translation', 'PreRotation', 'PostRotation', 'RotationOffset', 'RotationPivot',
                'ScalingOffset', 'ScalingPivot', 'GeometricTranslation', 'GeometricRotation'):
        require(effective.get(key) == [0., 0., 0.], 'unsupported FBX transform: ' + key)
    for key in ('Lcl Scaling', 'GeometricScaling'):
        require(effective.get(key) == [1., 1., 1.], 'unsupported FBX scale: ' + key)
    rotation = effective.get('Lcl Rotation')
    require(rotation and len(rotation) == 3 and rotation[:2] == [0., 0.]
            and math.isfinite(rotation[2]), 'audit supports the actual exporter Z-only model rotation')
    angle = math.radians(rotation[2])
    c, s = math.cos(angle), math.sin(angle)
    matrix = np.array(((c, -s, 0.), (s, c, 0.), (0., 0., 1.)), dtype=np.float64)
    axes = [int(settings[key][0]) for key in ('UpAxis', 'FrontAxis', 'CoordAxis')]
    signs = [int(settings[key][0]) for key in ('UpAxisSign', 'FrontAxisSign', 'CoordAxisSign')]
    require(sorted(axes) == [0, 1, 2] and all(value in (-1, 1) for value in signs),
            'FBX scene axes must define a signed axis permutation')
    materials = {item.props[0]: text(item.props[1]).split('\x00', 1)[0]
                 for item in objects.elems if item.id == b'Material'}
    slots = [materials[row[1]] for row in connections
             if row[0] == b'OO' and row[2] == model.props[0] and row[1] in materials]
    layer = child(geometry, b'LayerElementMaterial')
    require(child(layer, b'MappingInformationType').props[0] == b'ByPolygon'
            and child(layer, b'ReferenceInformationType').props[0] == b'IndexToDirect',
            'expected original per-polygon FBX material indices')
    material_indices = np.asarray(child(layer, b'Materials').props[0], dtype=np.int64)
    require(len(material_indices) == len(triangles) and np.all(material_indices >= 0)
            and np.all(material_indices < len(slots)), 'invalid FBX per-face material index')
    identities = np.asarray(slots)[material_indices]
    metadata = dict(version=version, parser_file=str(Path(parse.__file__)),
        unit_scale_factor_cm=unit_cm, settings=settings,
        models=[dict(uid=int(model.props[0]), name=text(model.props[1]), properties=properties(model),
                     template_properties=templates, effective_properties=effective)],
        root_model_without_parent_transform=True, geometry_directly_attached=True,
        global_axis_conversion_is_signed_permutation=True, material_slots=slots,
        model_rotation_matrix=matrix.tolist(), model_transform_calculation='float64 original FBX Z Euler angle, no angle snapping',
        native_interchange_vertex_positions_read=False)
    return vertices * (unit_cm / 100.), triangles, identities, matrix, metadata


def match_source_frame(source_points, fbx_vertices, fbx_triangles, source_materials=None, fbx_materials=None):
    """Verify every original triangle by exact geometry, allowing axis permutation.

    Both inputs originate as float32 coordinates. Baked FBX raw geometry may
    use the exporter's signed/permuted axes. No positional tolerance is accepted.
    """
    source_keys = face_keys(source_points)
    source_sorted = np.sort(source_keys)
    source_bounds = (source_points.min(axis=(0, 1)), source_points.max(axis=(0, 1)))
    for permutation in itertools.permutations(range(3)):
        for signs in itertools.product((-1., 1.), repeat=3):
            mapped = fbx_vertices[:, permutation] * signs
            points = mapped[fbx_triangles]
            if not (np.array_equal(points.min(axis=(0, 1)), source_bounds[0])
                    and np.array_equal(points.max(axis=(0, 1)), source_bounds[1])):
                continue
            keys = face_keys(points)
            if np.array_equal(np.sort(keys), source_sorted):
                material_equal = None
                if source_materials is not None:
                    material_equal = bool(np.array_equal(np.sort(material_face_keys(source_points, source_materials)),
                                                         np.sort(material_face_keys(points, fbx_materials))))
                    require(material_equal, 'FBX original per-face material/geometry multiset differs')
                return points, dict(axis_permutation=list(permutation), axis_signs=list(signs),
                                    exact_face_multiset_equal=True,
                                    exact_material_face_multiset_equal=material_equal,
                                    source_face_order_preserved=bool(np.array_equal(keys, source_keys)))
    raise ValueError('FBX original triangle geometry differs from source NPZ; no exact signed-axis correspondence')


def audit(scene_file, report):
    source = read(scene_file)
    require(source.get('id') == 'wt_vlaanderen', 'audit is scoped to original Flanders')
    meshes = [item for item in source['meshes'] if item['id'] == 'world_decal_000']
    require(len(meshes) == 1, 'world_decal_000 must exist exactly once')
    spec = meshes[0]
    require(spec.get('source') == 'baked_source_world', 'expected baked source-world mesh')
    package = scene_file.parent
    fbx = (package / spec['file']).resolve(strict=True)
    require(fbx.is_relative_to(package), 'FBX is outside source package')
    fbx_hash = sha(fbx)
    require(fbx_hash == spec['sha256'], 'FBX hash does not match manifest')
    extraction = Path(source['extraction_root'])
    if not extraction.is_absolute():
        extraction = (package / extraction).resolve()
    land_dir = extraction / 'landmesh-01'
    land_file = land_dir / 'landmesh.json'
    land = read(land_file)
    cells = [item for item in land['cells'] if item['kind'] == 'decal']
    report.update(mesh_id=spec['id'], fbx_file=spec['file'], fbx_sha256=fbx_hash,
                  source_sha256=source['source_sha256'], audited_manifest_sha256=sha(scene_file),
                  source_landmesh_file=str(land_file), source_landmesh_sha256=sha(land_file),
                  source_inputs=[], face_evidence=[], collinear_distinct_face_evidence=[])
    rmi = (package/spec['instances_file']).resolve(strict=True)
    require(rmi.is_relative_to(package), 'RMI escapes source package')
    rmi_bytes = rmi.read_bytes()
    require(len(rmi_bytes) == 48 and rmi_bytes[:8] == b'RMI1\x01\x00\x00\x00', 'expected exactly one RMI placement')
    row = struct.unpack_from('<10f', rmi_bytes, 8)
    require(row == (0., 0., 0., 0., 0., 0., 1., 1., 1., 1.), 'world decal RMI must be identity')
    report['rmi_placement'] = dict(file=spec['instances_file'], sha256=sha(rmi), identity=True,
                                  count=1, transform=list(row))
    arrays, material_arrays, offset = [], [], 0
    all_materials = np.asarray([material_id(item) for item in land['materials']])
    for entry in cells:
        path = (land_dir / entry['file']).resolve(strict=True)
        require(path.is_relative_to(land_dir.resolve()), 'source NPZ escapes original landmesh directory')
        source_bytes = path.read_bytes()
        with np.load(io.BytesIO(source_bytes), allow_pickle=False) as archive:
            original = archive['vertices']
            require(original.dtype == np.dtype('float32'), 'expected exact original float32 positions')
            vertices = original.astype(np.float64)
            require(archive['triangles'].dtype.kind in 'iu' and archive['material_ids'].dtype.kind in 'iu',
                    'source indices must have integer dtype')
            triangles = archive['triangles'].astype(np.int64)
            material_ids = archive['material_ids'].astype(np.int64)
        require(vertices.ndim == 2 and vertices.shape[1] == 3, 'source vertex shape differs')
        require(triangles.ndim == 2 and triangles.shape[1] == 3, 'source triangle shape differs')
        require(len(triangles) == entry['triangles'] and len(vertices) == entry['vertices'], 'source inventory counts differ')
        require(len(material_ids) == len(triangles), 'source material-per-face array differs')
        require(material_ids.ndim == 1 and np.all(material_ids >= 0)
                and np.all(material_ids < len(all_materials)), 'source material index outside inventory')
        require(np.all(triangles >= 0) and np.all(triangles < len(vertices)), 'source index outside vertex array')
        points = vertices[triangles]
        require(np.all(np.isfinite(points)), 'source contains non-finite positions')
        coincident, zero, area = triangle_metrics(points)
        arrays.append(points)
        material_arrays.append(all_materials[material_ids])
        report['source_inputs'].append(dict(file=str(path), sha256=hashlib.sha256(source_bytes).hexdigest(), cell=entry['cell'],
            vertices=len(vertices), triangles=len(triangles), first_batch_triangle=offset,
            strict_coincident=int(coincident.sum()), zero_area=int(zero.sum())))
        for index in np.flatnonzero(zero):
            require(exact_zero_area(points[index]), 'float64 zero-area candidate fails exact rational cross product')
            item = dict(source_file=entry['file'], cell=int(entry['cell']), source_face_index=int(index),
                        batch_face_index=offset+int(index), original_vertex_indices=triangles[index].tolist(),
                        positions_dagor_m=points[index].tolist(), area_m2=float(area[index]),
                        source_material_index=int(material_ids[index]),
                        stable_material_id=material_id(land['materials'][int(material_ids[index])]))
            item['exact_rational_zero_area_verified'] = True
            report['face_evidence' if coincident[index] else 'collinear_distinct_face_evidence'].append(item)
        offset += len(triangles)
    require(offset == spec['triangles'], 'all original decal cells must exactly fill world_decal_000')
    source_points = np.concatenate(arrays)
    source_materials = np.concatenate(material_arrays)
    source_pair, source_zero, source_area = triangle_metrics(source_points)
    vertices, triangles, fbx_materials, model_rotation, fbx_metadata = read_fbx(fbx)
    report['fbx_metadata'] = fbx_metadata
    require(len(triangles) == offset, 'FBX polygon count differs from complete original source')
    require(len(vertices) == spec['vertices'], 'FBX control-point count differs from prepared manifest')
    require(fbx_metadata['material_slots'] == spec['material_slots'], 'FBX material slot identities/order differ from manifest')
    fbx_source_points, correspondence = match_source_frame(source_points, vertices, triangles, source_materials, fbx_materials)
    fbx_pair, fbx_zero, _ = triangle_metrics(fbx_source_points)
    # Dagor -> ROS (x,-z,y) -> UE (100*x,100*z,100*y). This is the target
    # vertex-position storage specified by the importer; actor transform is identity.
    ue_cm = (fbx_source_points[:, :, (0, 2, 1)] * 100.).astype(np.float32).astype(np.float64)
    quant_pair, quant_zero, quant_area = triangle_metrics(ue_cm)
    extra_pair = quant_pair & ~fbx_pair
    positive_collapsed = quant_zero & ~fbx_zero
    # Global scene axis conversion is a signed permutation, which preserves
    # equality/zero-area results and commutes with final componentwise float32
    # rounding. Exercise the actual unsnapped model rotation in FBX space, both
    # possible double-precision orders of unit conversion. No native UE vertices
    # are read here; the importer must still meet the exact native triangle target.
    model_variants = {
        'rotate_then_centimetres': ((vertices @ model_rotation.T) * 100.)[triangles],
        'centimetres_then_rotate': ((vertices * 100.) @ model_rotation.T)[triangles]}
    model_extra = np.zeros(len(triangles), dtype=bool)
    model_collapsed = np.zeros(len(triangles), dtype=bool)
    model_records = {}
    for name, model_points in model_variants.items():
        pair, zero, _ = triangle_metrics(model_points.astype(np.float32).astype(np.float64))
        extra, collapsed = pair & ~fbx_pair, zero & ~fbx_zero
        model_extra |= extra
        model_collapsed |= collapsed
        model_records[name] = dict(strict_coincident_after_float32=int(pair.sum()),
                                  extra_float32_coincident=int(extra.sum()),
                                  positive_area_collapsed_after_float32=int(collapsed.sum()))
    report.update(source_triangles=offset, source_file_count=len(cells),
        strict_coincident=int(source_pair.sum()), zero_area=int(source_zero.sum()),
        zero_area_with_distinct_positions=int((source_zero & ~source_pair).sum()),
        fbx_triangles=len(triangles), fbx_vertices=len(vertices),
        fbx_strict_coincident=int(fbx_pair.sum()), fbx_zero_area=int(fbx_zero.sum()),
        extra_float32_coincident=int(extra_pair.sum()),
        extra_float32_coincident_face_indices=np.flatnonzero(extra_pair).tolist(),
        positive_area_collapsed_after_float32=int(positive_collapsed.sum()),
        positive_area_collapsed_face_indices=np.flatnonzero(positive_collapsed).tolist(),
        model_transform_extra_float32_coincident=int(model_extra.sum()),
        model_transform_extra_float32_coincident_face_indices=np.flatnonzero(model_extra).tolist(),
        model_transform_positive_area_collapsed_after_float32=int(model_collapsed.sum()),
        model_transform_positive_area_collapsed_face_indices=np.flatnonzero(model_collapsed).tolist(),
        model_transform_quantization_variants=model_records,
        strict_coincident_source_face_indices=np.flatnonzero(source_pair).tolist(),
        strict_coincident_fbx_face_indices=np.flatnonzero(fbx_pair).tolist(),
        expected_native_triangles=offset-int(source_pair.sum()),
        minimum_positive_source_area_m2=float(source_area[source_area > 0.].min()),
        minimum_positive_quantized_area_cm2=float(quant_area[quant_area > 0.].min()),
        fbx_source_correspondence=correspondence,
        index_domains=dict(source='concatenated original decal cells', fbx='original binary FBX polygon order',
                           quantization='original binary FBX polygon order'),
        source_material_triangle_counts={str(label): int(np.sum(source_materials == label)) for label in np.unique(source_materials)},
        source_material_coincident_counts={str(label): int(np.sum(source_pair & (source_materials == label))) for label in np.unique(source_materials)},
        float32_quantization_basis='original world geometry -> UE cm (100*x,100*z,100*y), float32 storage',
        accounting_rule='source_triangles - strictly_coincident_source_faces; distinct-position collinear faces remain counted')
    require(int(fbx_pair.sum()) == int(source_pair.sum()), 'FBX changes source coincident-face count')
    require(int(fbx_zero.sum()) == int(source_zero.sum()), 'FBX changes source zero-area-face count')
    require(not np.any(extra_pair), 'positive source edge collapses after UE cm float32 quantization')
    require(not np.any(positive_collapsed), 'positive source area collapses after UE cm float32 quantization')
    require(not np.any(model_extra), 'model transform produces additional float32 coincident faces')
    require(not np.any(model_collapsed), 'model transform collapses positive source area after float32 storage')
    require(sha(fbx) == fbx_hash, 'FBX bytes changed while auditing')
    report['status'] = 'passed'


def annotation_fields(scene_file, proof_file):
    """Return fields for a caller to attach; never modify the manifest here.

    Copy a passed proof inside the package first. The manifest digest in the
    proof is informational: adding this annotation necessarily changes it.
    """
    scene_file, proof_file = Path(scene_file).resolve(), Path(proof_file).resolve()
    source, proof = read(scene_file), read(proof_file)
    require(proof['schema'] == SCHEMA and proof['status'] == 'passed', 'proof did not pass')
    spec = next(item for item in source['meshes'] if item['id'] == proof['mesh_id'])
    require(spec['sha256'] == proof['fbx_sha256'] and spec['triangles'] == proof['source_triangles'], 'proof differs from current mesh')
    require(source['source_sha256'] == proof['source_sha256'], 'original scene provenance differs')
    relative = proof_file.relative_to(scene_file.parent)
    return dict(geometry_audit_file=relative.as_posix(), geometry_audit_sha256=sha(proof_file))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene-source', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    argv = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    output = args.output_dir/'source-proof.json'
    report = dict(schema=SCHEMA, status='running', unreal_executed=False,
                  scope='original_NPZ_vs_binary_FBX_and_target_UE_float32_geometry',
                  audit_script_sha256=sha(Path(__file__).resolve()))
    failure = None
    try:
        audit(args.scene_source.resolve(strict=True), report)
    except Exception:
        failure = traceback.format_exc()
        report.update(status='failed', error=failure)
    with output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + '\n')
    omit = {'source_inputs', 'face_evidence', 'collinear_distinct_face_evidence', 'fbx_metadata',
            'source_material_triangle_counts', 'source_material_coincident_counts'}
    summary = {key: value for key, value in report.items() if key not in omit}
    summary.update(schema='wt_source_degenerate_face_audit_summary_v1', proof_file='source-proof.json',
                   proof_sha256=sha(output), source_input_files=len(report.get('source_inputs', [])))
    with (args.output_dir/'verification.json').open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    print(json.dumps(summary, indent=2))
    if failure:
        raise RuntimeError(failure)


if __name__ == '__main__':
    main()
