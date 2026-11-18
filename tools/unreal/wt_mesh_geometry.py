"""Bind exact native triangle accounting to independently audited source faces."""
import hashlib
import json
import math
from pathlib import Path
import re


SCHEMA = 'wt_source_degenerate_face_audit_v1'


def _require(condition, message):
    if not condition:
        raise ValueError('source geometry audit: ' + message)


def _integer(value, label, lower=0):
    _require(type(value) is int and value >= lower, label + ' must be an integer')
    return value


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _local(package, relative):
    _require(isinstance(relative, str) and relative != '', 'missing package-relative path')
    path = Path(relative)
    _require(not path.is_absolute(), 'absolute audit/input path is not allowed')
    resolved = (package / path).resolve(strict=True)
    _require(resolved.is_relative_to(package), 'audit/input path escapes source package')
    _require(resolved.is_file(), 'audit/input path must be a file')
    return resolved


def _digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def triangle_accounting(package, spec, scene_source_sha256=None):
    """Keep original face counts; subtract only hash-bound, proven coincident faces.

    Distinct-position collinear triangles remain in the native target. A normal
    mesh has no exception and must preserve its complete original triangle count.
    """
    source_count = _integer(spec['triangles'], 'source triangle count', 1)
    fields = ('geometry_audit_file', 'geometry_audit_sha256')
    present = [name in spec for name in fields]
    result = dict(source_triangles=source_count, expected_native_triangles=source_count,
                  strictly_coincident_source_triangles=0, geometry_audit_sha256=None)
    if not any(present):
        return result
    _require(all(present), 'both proof path and proof hash are required')
    _require(_sha(scene_source_sha256), 'original scene SHA256 is required')
    _require(_sha(spec['geometry_audit_sha256']), 'invalid proof SHA256')
    package = Path(package).resolve(strict=True)
    proof_file = _local(package, spec['geometry_audit_file'])
    _require(_digest(proof_file) == spec['geometry_audit_sha256'], 'proof bytes differ from manifest hash')
    proof = json.loads(proof_file.read_text(encoding='utf-8-sig'))
    _require(proof.get('schema') == SCHEMA and proof.get('status') == 'passed', 'proof did not pass')
    for key, expected in [('mesh_id', spec['id']), ('source_sha256', scene_source_sha256),
                          ('fbx_sha256', spec['sha256']), ('fbx_file', spec['file']),
                          ('source_triangles', source_count), ('fbx_triangles', source_count)]:
        _require(proof.get(key) == expected, key + ' differs from the source mesh')
    _require(_digest(_local(package, spec['file'])) == spec['sha256'], 'FBX bytes differ from audited input')
    removed = _integer(proof.get('strict_coincident'), 'strict coincident count')
    _require(removed < source_count, 'audit must retain renderable source triangles')
    _require(proof.get('fbx_strict_coincident') == removed, 'FBX coincident-face count differs')
    _require(proof.get('expected_native_triangles') == source_count - removed, 'native target is inconsistent')
    _require(proof.get('fbx_source_correspondence', {}).get('exact_face_multiset_equal') is True,
             'complete source/FBX face correspondence is required')
    for key in ('extra_float32_coincident', 'positive_area_collapsed_after_float32',
                'model_transform_extra_float32_coincident',
                'model_transform_positive_area_collapsed_after_float32'):
        _require(type(proof.get(key)) is int and proof[key] == 0, key + ' must be zero')
    index_sets = []
    for key in ('strict_coincident_source_face_indices', 'strict_coincident_fbx_face_indices'):
        indices = proof.get(key)
        _require(isinstance(indices, list) and len(indices) == removed, key + ' is incomplete')
        _require(all(type(index) is int and 0 <= index < source_count for index in indices),
                 key + ' contains an invalid face index')
        _require(len(set(indices)) == removed, key + ' contains repeated face indices')
        index_sets.append(set(indices))
    faces = proof.get('face_evidence')
    _require(isinstance(faces, list) and len(faces) == removed, 'coincident face evidence is incomplete')
    face_indices = set()
    removed_by_material = {}
    for face in faces:
        points = face.get('positions_dagor_m')
        _require(isinstance(points, list) and len(points) == 3 and
                 all(isinstance(point, list) and len(point) == 3 for point in points),
                 'face must contain three original positions')
        _require(all(type(value) in (int, float) and math.isfinite(value)
                     for point in points for value in point), 'face coordinates must be finite')
        _require(points[0] == points[1] or points[0] == points[2] or points[1] == points[2],
                 'exception face has no strictly coincident source vertices')
        index = _integer(face.get('batch_face_index'), 'source face index')
        _require(index not in face_indices, 'repeated source face evidence')
        face_indices.add(index)
        identity = face.get('stable_material_id')
        _require(identity in spec['material_slots'], 'exception face references unknown source material')
        removed_by_material[identity] = removed_by_material.get(identity, 0) + 1
    _require(face_indices == index_sets[0], 'source face evidence does not cover audited indices')
    inputs = proof.get('source_inputs')
    _require(isinstance(inputs, list) and len(inputs) > 0, 'original input hashes are required')
    _require(proof.get('source_file_count') == len(inputs), 'source file count differs')
    _require(all(_sha(item.get('sha256')) for item in inputs), 'invalid original input hash')
    _require(sum(_integer(item.get('triangles'), 'source input triangle count') for item in inputs)
             == source_count, 'original input counts do not cover the complete mesh')
    material_counts = proof.get('source_material_triangle_counts')
    _require(isinstance(material_counts, dict) and len(material_counts) > 0,
             'source per-material triangle counts are required')
    _require(set(material_counts).issubset(spec['material_slots']), 'source material inventory differs')
    _require(sum(_integer(value, 'source material count') for value in material_counts.values()) == source_count,
             'source material counts do not cover the mesh')
    declared_removed = proof.get('source_material_coincident_counts')
    _require(isinstance(declared_removed, dict), 'source material coincident counts are required')
    _require({key: _integer(value, 'coincident material count') for key, value in declared_removed.items() if value}
             == removed_by_material, 'per-material exception counts differ from face evidence')
    expected_material_counts = {key: count - removed_by_material.get(key, 0)
                                for key, count in material_counts.items()}
    _require(all(count >= 0 for count in expected_material_counts.values()), 'negative material triangle target')
    result.update(expected_native_triangles=source_count - removed,
                  strictly_coincident_source_triangles=removed,
                  geometry_audit_file=spec['geometry_audit_file'],
                  geometry_audit_sha256=spec['geometry_audit_sha256'],
                  expected_native_material_triangles=expected_material_counts)
    return result


def verify_native_material_triangles(accounting, native_geometry):
    """Compare actual rendered sections with source identity-based face counts."""
    expected = accounting.get('expected_native_material_triangles')
    if expected is None:
        return None
    actual = {}
    for section in native_geometry['sections']:
        identity = section['material_slot']
        actual[identity] = actual.get(identity, 0) + _integer(section['triangles'], 'native section triangle count')
    _require({key: count for key, count in actual.items() if count}
             == {key: count for key, count in expected.items() if count},
             'native material-section triangles differ from audited original faces')
    return actual
