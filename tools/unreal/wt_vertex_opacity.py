"""Validate the source-backed landmesh vertex opacity transport before UE import."""
import hashlib
import json
import math
from pathlib import Path
import re


HEIGHT_DECAL_SHADER = 'land_mesh_height_decal'
OPACITY_SCHEMA = 'wt_landmesh_vertex_opacity_v1'
PROOF_SCHEMA = 'wt_landmesh_vertex_opacity_verification_v1'


def _require(condition, message):
    if not condition:
        raise ValueError('source vertex opacity: ' + message)


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _local(package, relative):
    _require(isinstance(relative, str) and relative != '', 'missing package-relative file')
    path = Path(relative)
    _require(not path.is_absolute(), 'absolute proof/input path is not allowed')
    resolved = (package / path).resolve(strict=True)
    _require(resolved.is_relative_to(package) and resolved.is_file(), 'proof/input escapes package or is not a file')
    return resolved


def _digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def validate_vertex_opacity(package, spec, scene_source_sha256):
    contract = spec.get('source_vertex_opacity')
    if contract is None:
        return None
    _require(isinstance(contract, dict), 'contract must be an object')
    for key, expected in [('schema', OPACITY_SCHEMA), ('shader', HEIGHT_DECAL_SHADER),
                          ('uv_channel', 1), ('component', 'x'),
                          ('source', 'short4n_position_w'), ('full_precision', True)]:
        _require(contract.get(key) == expected, 'unsupported ' + key)
    _require(_sha(scene_source_sha256), 'original level hash is required')
    _require(_sha(contract.get('raw_verification_sha256')), 'invalid raw verification hash')
    package = Path(package).resolve(strict=True)
    proof_file = _local(package, contract.get('raw_verification_file'))
    _require(_digest(proof_file) == contract['raw_verification_sha256'], 'proof bytes differ from manifest hash')
    proof = json.loads(proof_file.read_text(encoding='utf-8-sig'))
    _require(proof.get('schema') == PROOF_SCHEMA and proof.get('status') == 'passed', 'proof did not pass')
    triangles = spec.get('triangles')
    _require(type(triangles) is int and triangles > 0, 'invalid original triangle count')
    for key, expected in [('mesh_id', spec['id']), ('source_sha256', scene_source_sha256),
                          ('fbx_file', spec['file']), ('fbx_sha256', spec['sha256']),
                          ('source_triangles', triangles), ('fbx_triangles', triangles),
                          ('source_loop_count', triangles * 3), ('fbx_loop_count', triangles * 3),
                          ('uv_channel', 1), ('component', 'x'), ('full_precision_required', True)]:
        _require(proof.get(key) == expected, key + ' differs from the source contract')
    _require(_digest(_local(package, spec['file'])) == spec['sha256'], 'FBX bytes differ from opacity proof')
    for field in ('geometry_arrays_unchanged', 'uv0_unchanged', 'material_slots_unchanged'):
        _require(proof.get(field) is True, field + ' was not established')
    for field, limit in [('uv1_max_abs_error', 1e-7), ('uv1_y_max_abs_error', 0.0)]:
        value = proof.get(field)
        _require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= limit,
                 field + ' exceeds the source transport limit')
    for field in ('raw_source_report_sha256', 'raw_payload_sha256'):
        _require(_sha(proof.get(field)), 'missing original ' + field)
    return dict(contract=contract, proof_sha256=contract['raw_verification_sha256'],
                source_loop_count=triangles * 3, expected_uv_channels=2,
                max_abs_error=proof['uv1_max_abs_error'])


def validate_scene_vertex_opacity(package, source):
    """Every source height-decal use must supply the verified original pos.w."""
    height_ids = {item['id'] for item in source['materials'] if item['shader'] == HEIGHT_DECAL_SHADER}
    used, records = set(), {}
    for spec in source.get('meshes', []):
        identities = height_ids.intersection(spec['material_slots'])
        contract = spec.get('source_vertex_opacity')
        if identities:
            _require(contract is not None, spec['id'] + ' is missing original height-decal vertex opacity')
            records[spec['id']] = validate_vertex_opacity(package, spec, source['source_sha256'])
            used.update(identities)
        elif contract is not None:
            raise ValueError('source vertex opacity: contract attached to a mesh without height-decal materials')
    _require(used == height_ids, 'source height-decal material has no verified mesh use')
    return records
