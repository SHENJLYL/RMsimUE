"""Append source position.w as a full-precision UV contract to one Flanders FBX.

Uses Blender's official binary parser/writer. Existing geometry, materials,
UV0 and transforms are compared byte-for-byte as decoded property arrays.
Original game/extraction files are read-only. Publish follows all staging checks.
"""
import argparse
import copy
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys
import numpy as np
from io_scene_fbx import parse_fbx, encode_bin, data_types

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_wt_degenerate_faces as geometry_audit


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def child(node, identity):
    return geometry_audit.child(node, identity)


METHODS = {
    data_types.BOOL:'add_bool', data_types.CHAR:'add_char', data_types.INT8:'add_int8',
    data_types.INT16:'add_int16', data_types.INT32:'add_int32', data_types.INT64:'add_int64',
    data_types.FLOAT32:'add_float32', data_types.FLOAT64:'add_float64',
    data_types.BYTES:'add_bytes', data_types.STRING:'add_string',
    data_types.INT32_ARRAY:'add_int32_array', data_types.INT64_ARRAY:'add_int64_array',
    data_types.FLOAT32_ARRAY:'add_float32_array', data_types.FLOAT64_ARRAY:'add_float64_array',
    data_types.BOOL_ARRAY:'add_bool_array', data_types.BYTE_ARRAY:'add_byte_array',
}


def encode_copy(node):
    result = encode_bin.FBXElem(node.id)
    for kind, value in zip(node.props_type, node.props):
        require(kind in METHODS, 'unsupported original FBX property type')
        getattr(result, METHODS[kind])(value)
    result.elems = [encode_copy(c) for c in node.elems]
    return result


def add(parent, identity, method=None, value=None):
    result = encode_bin.FBXElem(identity)
    if method:
        getattr(result, method)(value)
    parent.elems.append(result)
    return result


def fingerprint(node, exclude_added_uv=False):
    h = hashlib.sha256()
    def visit(item):
        if item.id in (b'FileId', b'CreationTime'):
            return
        if exclude_added_uv and item.id in (b'LayerElementUV', b'Layer') and item.props == [1]:
            return
        h.update(struct.pack('<I', len(item.id))); h.update(item.id); h.update(bytes(item.props_type))
        for value in item.props:
            if isinstance(value, bytes):
                blob = value
            elif hasattr(value, 'tobytes'):
                blob = value.tobytes()
            elif isinstance(value, float):
                blob = struct.pack('<d', value)
            else:
                blob = str(value).encode()
            h.update(struct.pack('<Q', len(blob))); h.update(blob)
        for c in item.elems:
            visit(c)
        h.update(b'\x00END')
    visit(node)
    return h.hexdigest()


def atomic_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name+'.opacity-tmp')
    require(not temp.exists(), 'temporary publish path already exists')
    shutil.copy2(source, temp)
    temp.replace(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene-source', type=Path, required=True)
    parser.add_argument('--raw-report', type=Path, required=True)
    parser.add_argument('--evidence-root', type=Path, required=True)
    argv = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    manifest = args.scene_source.resolve(strict=True); package = manifest.parent
    evidence = args.evidence_root.resolve(); evidence.mkdir(parents=True, exist_ok=False)
    stage = evidence/'staging'; stage.mkdir()
    report = dict(schema='wt_decal_opacity_adaptation_v1', status='running', unreal_executed=False,
                  started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  adapter_script_sha256=sha(Path(__file__)), package=str(package), backups=[])
    try:
        source = read(manifest); original = copy.deepcopy(source)
        spec = next(m for m in source['meshes'] if m['id'] == 'world_decal_000')
        require('source_vertex_opacity' not in spec, 'opacity already adapted; use the retained before package for a new audit')
        fbx = package/spec['file']; old_proof_file = package/spec['geometry_audit_file']
        require(sha(fbx) == spec['sha256'] and sha(old_proof_file) == spec['geometry_audit_sha256'],
                'original package differs from manifest')
        raw_path = args.raw_report.resolve(strict=True); raw_report = read(raw_path)
        require(raw_report['status'] == 'passed' and raw_report['source_sha256'] == source['source_sha256'],
                'raw source report did not pass matching original identity')
        require(raw_report['source_position_max_abs_error_m'] == 0 and raw_report['original_input_hashes_unchanged'],
                'raw recovery did not exactly preserve original extraction')
        payload = raw_path.parent/raw_report['payload_file']
        require(sha(payload) == raw_report['payload_sha256'], 'raw payload bytes differ')
        for item in raw_report['source_inputs']:
            require(sha(Path(item['path'])) == item['sha256'], 'original NPZ bytes changed')
        for file in (manifest, fbx, old_proof_file):
            relative = file.relative_to(package); backup = evidence/'before'/relative
            backup.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(file, backup)
            report['backups'].append(dict(file=str(relative), path=str(backup), sha256=sha(backup)))
        report['before_manifest_sha256'] = sha(manifest)
        old_root, version = parse_fbx.parse(str(fbx))
        old_geometry = next(n for n in child(old_root, b'Objects').elems if n.id == b'Geometry')
        uv_layers = [n for n in old_geometry.elems if n.id == b'LayerElementUV']
        require(len(uv_layers) == 1 and uv_layers[0].props == [0], 'original FBX must have only UV0')
        fbx_vertices, fbx_triangles, fbx_materials, matrix, metadata = geometry_audit.read_fbx(fbx)
        with np.load(payload, allow_pickle=False) as data:
            source_vertices = data['vertices_dagor_m'].copy()
            source_triangles = data['triangles'].copy()
            opacity = data['opacity_w'].copy()
            raw_short = data['position_w_i16'].copy()
        require(np.array_equal(opacity, np.maximum(raw_short.astype(np.float32)/np.float32(32767.), -1.)),
                'raw normalized opacity differs from original short4n')
        # Mirror only the established preservation of two repeated source draws.
        _, first = np.unique(np.sort(source_triangles, axis=1), axis=0, return_index=True)
        repeated = np.setdiff1d(np.arange(len(source_triangles)), first, assume_unique=True)
        require(len(repeated) == spec['duplicate_faces_preserved'], 'duplicate source draw inventory differs')
        duplicates = source_triangles[repeated].ravel()
        expected_vertices = np.concatenate((source_vertices, source_vertices[duplicates]))
        expected_opacity = np.concatenate((opacity, opacity[duplicates]))
        old_proof = read(old_proof_file); frame = old_proof['fbx_source_correspondence']
        mapped = fbx_vertices[:, frame['axis_permutation']]*frame['axis_signs']
        require(np.array_equal(mapped, expected_vertices), 'FBX control points do not exactly match original vertex order')
        require(len(fbx_vertices) == spec['vertices'] and len(fbx_triangles) == spec['triangles'] == 87864,
                'original FBX inventory differs')
        require(metadata['material_slots'] == spec['material_slots'], 'original FBX material slots differ')
        root = encode_copy(old_root)
        geometry = next(n for n in child(root, b'Objects').elems if n.id == b'Geometry')
        uv1 = add(geometry, b'LayerElementUV', 'add_int32', 1)
        add(uv1, b'Version', 'add_int32', 101)
        add(uv1, b'Name', 'add_string', b'UV1')
        add(uv1, b'MappingInformationType', 'add_string', b'ByPolygonVertex')
        add(uv1, b'ReferenceInformationType', 'add_string', b'IndexToDirect')
        coordinates = np.column_stack((expected_opacity.astype(np.float64), np.ones(len(expected_opacity))))
        add(uv1, b'UV', 'add_float64_array', coordinates.ravel())
        add(uv1, b'UVIndex', 'add_int32_array', fbx_triangles.ravel().astype(np.int32))
        layer = add(geometry, b'Layer', 'add_int32', 1); add(layer, b'Version', 'add_int32', 100)
        binding = add(layer, b'LayerElement')
        add(binding, b'Type', 'add_string', b'LayerElementUV')
        add(binding, b'TypedIndex', 'add_int32', 1)
        staged_fbx = stage/spec['file']; staged_fbx.parent.mkdir(parents=True)
        encode_bin.write(str(staged_fbx), root, version)
        new_root, new_version = parse_fbx.parse(str(staged_fbx))
        require(new_version == version, 'FBX version changed')
        require(fingerprint(old_root) == fingerprint(new_root, exclude_added_uv=True),
                'an existing FBX element changed during UV append')
        new_geometry = next(n for n in child(new_root, b'Objects').elems if n.id == b'Geometry')
        actual_uv1 = next(n for n in new_geometry.elems if n.id == b'LayerElementUV' and n.props == [1])
        direct = np.asarray(child(actual_uv1, b'UV').props[0], dtype=np.float64).reshape(-1, 2)
        loop_indices = np.asarray(child(actual_uv1, b'UVIndex').props[0], dtype=np.int64)
        loop_values = direct[loop_indices]
        expected_loop_values = expected_opacity[fbx_triangles.ravel()].astype(np.float64)
        error = float(np.abs(loop_values[:, 0]-expected_loop_values).max())
        y_error = float(np.abs((1-loop_values[:, 1])).max())
        require(error == 0 and y_error == 0, 'UV1 failed exact source-loop readback')
        new_hash = sha(staged_fbx)
        proof_relative = 'geometry-audits/world_decal_000-opacity.json'
        opacity_proof = dict(schema='wt_landmesh_vertex_opacity_verification_v1', status='passed',
            source_sha256=source['source_sha256'], mesh_id=spec['id'], fbx_file=spec['file'], fbx_sha256=new_hash,
            source_triangles=len(source_triangles), fbx_triangles=len(fbx_triangles),
            source_loop_count=int(source_triangles.size), fbx_loop_count=len(loop_values),
            uv_channel=1, component='x', source='short4n_position_w', full_precision_required=True,
            uv1_max_abs_error=error, uv1_y_max_abs_error=y_error, fbx_uv1_v_constant=1,
            geometry_arrays_unchanged=True, uv0_unchanged=True, material_slots_unchanged=True,
            all_existing_fbx_elements_unchanged=True, source_position_max_abs_error_m=0,
            source_inputs=raw_report['source_inputs'], source_vertices=len(source_vertices),
            fbx_vertices=len(fbx_vertices), duplicate_faces_preserved=len(repeated),
            original_fbx_sha256=spec['sha256'], original_geometry_proof_sha256=spec['geometry_audit_sha256'],
            raw_source_report_sha256=sha(raw_path), raw_payload_sha256=sha(payload),
            raw_source_report_file=str(raw_path), raw_payload_file=str(payload),
            existing_fbx_elements_sha256=fingerprint(old_root), adapter_script_sha256=sha(Path(__file__)),
            parser_sha256=sha(Path(parse_fbx.__file__)), writer_sha256=sha(Path(encode_bin.__file__)),
            opacity_min=float(opacity.min()), opacity_max=float(opacity.max()),
            raw_w_unique_count=raw_report['raw_w_unique_count'], spawn_faces=raw_report['spawn_faces'])
        save(stage/proof_relative, opacity_proof)
        spec['sha256'] = new_hash
        spec['source_vertex_opacity'] = dict(schema='wt_landmesh_vertex_opacity_v1', shader='land_mesh_height_decal',
            uv_channel=1, component='x', source='short4n_position_w', full_precision=True,
            raw_verification_file=proof_relative, raw_verification_sha256=sha(stage/proof_relative))
        staged_manifest = stage/manifest.name
        instance = stage/spec['instances_file']; instance.parent.mkdir(parents=True)
        shutil.copy2(package/spec['instances_file'], instance)
        save(staged_manifest, source)
        geometry_proof = dict(schema=geometry_audit.SCHEMA, status='running', unreal_executed=False,
            scope='original_NPZ_vs_binary_FBX_and_target_UE_float32_geometry',
            audit_script_sha256=sha(Path(geometry_audit.__file__)))
        geometry_audit.audit(staged_manifest, geometry_proof)
        require(geometry_proof['status'] == 'passed' and geometry_proof['strict_coincident'] == 34
                and geometry_proof['expected_native_triangles'] == 87830, 'source-face accounting changed')
        save(stage/spec['geometry_audit_file'], geometry_proof)
        save(evidence/'source-proof.json', geometry_proof)
        spec['geometry_audit_sha256'] = sha(stage/spec['geometry_audit_file'])
        save(staged_manifest, source)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'unreal'))
        from wt_mesh_geometry import triangle_accounting
        accounting = triangle_accounting(stage, spec, source['source_sha256'])
        changed = [m for m, previous in zip(source['meshes'], original['meshes']) if m != previous]
        require(len(changed) == 1 and changed[0]['id'] == 'world_decal_000', 'unrelated mesh records changed')
        require({k:v for k,v in source.items() if k != 'meshes'} ==
                {k:v for k,v in original.items() if k != 'meshes'}, 'unrelated manifest data changed')
        require(sha(manifest) == report['before_manifest_sha256'] and sha(fbx) == opacity_proof['original_fbx_sha256'],
                'package changed concurrently before publish')
        # Publish only after the independent complete source proof succeeds.
        for relative in (spec['file'], proof_relative, spec['geometry_audit_file'], manifest.name):
            atomic_copy(stage/relative, package/relative)
        require(sha(fbx) == new_hash and sha(package/proof_relative) == spec['source_vertex_opacity']['raw_verification_sha256'],
                'published bytes differ from checked staging')
        report.update(status='passed', source_sha256=source['source_sha256'], mesh_id=spec['id'],
            before_fbx_sha256=opacity_proof['original_fbx_sha256'], after_fbx_sha256=new_hash,
            after_manifest_sha256=sha(manifest), raw_source_report_sha256=sha(raw_path), raw_payload_sha256=sha(payload),
            opacity_proof_file=proof_relative, opacity_proof_sha256=sha(package/proof_relative),
            geometry_proof_file=spec['geometry_audit_file'], geometry_proof_sha256=spec['geometry_audit_sha256'],
            source_triangles=len(source_triangles), fbx_triangles=len(fbx_triangles),
            source_vertices=len(source_vertices), fbx_vertices=len(fbx_vertices),
            source_loop_count=int(source_triangles.size), fbx_loop_count=len(loop_values),
            uv1_max_abs_error=error, uv1_y_max_abs_error=y_error,
            geometry_arrays_unchanged=True, uv0_unchanged=True, material_slots_unchanged=True,
            unaffected_manifest_records_unchanged=True, existing_fbx_elements_sha256=fingerprint(old_root),
            strict_coincident=geometry_proof['strict_coincident'],
            expected_native_triangles=accounting['expected_native_triangles'],
            raw_opacity_min=float(opacity.min()), raw_opacity_max=float(opacity.max()),
            raw_w_unique_count=raw_report['raw_w_unique_count'], spawn_faces=raw_report['spawn_faces'])
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        report['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(evidence/'verification.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
