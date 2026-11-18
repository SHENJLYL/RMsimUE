"""Blender: adapt three original facing-leaf models to explicit reference cards.

The recovered source delta is retained in UV3/UV4 for camera-facing material WPO.
Only these three FBXs and their scene mesh records are replaced after roundtrips.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys

import bpy
import numpy as np

MODELS = {
    "kh_gol_euraspen_fall_03_rendinst": (153, 104, 31, 593),
    "kh_gol_liveoak_03_rendinst": (166, 84, 40, 1171),
    "kh_gol_tree_simple_a": (98, 64, 20, 605),
}
CONVERSION = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=np.float64)
CONTRACT = {
    "version": 1,
    "mode": "camera_no_wind",
    "source_shader": "rendinst_facing_leaves",
    "delta_xy_uv": 3,
    "delta_z_wind_uv": 4,
    "reference_delta_dagor": "[dx,dy,-dz]",
    "reference_delta_ros": "[dx,dz,dy]",
    "ue_uv_values": "raw_delta_m_and_wind_number",
}


def sha(path):
    with path.open("rb") as source:
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def bounds(points):
    return {"lo": points.min(axis=0).tolist(), "hi": points.max(axis=0).tolist()}


def material_id(material):
    fields = {key: material[key] for key in ("shader", "parameters", "texture_slots")}
    encoded = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    return "WT_" + hashlib.sha256(encoded).hexdigest()[:20]


def prepare(name, original, payload, metadata, spec):
    source_vertices = original["vertices"]
    if not np.array_equal(payload["positions"], source_vertices):
        raise ValueError("payload/source positions differ: " + name)
    vertices = source_vertices.astype(np.float64).copy()
    faces = original["triangles"].copy()
    source_normals = original["normals"]
    dot = np.sum(np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]],
                          vertices[faces[:, 2]] - vertices[faces[:, 0]]) *
                 source_normals[faces].mean(axis=1), axis=1)
    flip = dot < -1e-10
    faces[flip] = faces[flip][:, [0, 2, 1]]
    leaf_mask = payload["leaf_mask"]
    leaf_faces = payload["leaf_face_mask"]
    delta = payload["delta_xyzw"]
    offset = delta[:, :3].astype(np.float64) * np.array([1, 1, -1])
    vertices[leaf_mask] += offset[leaf_mask]
    if not np.array_equal(vertices[~leaf_mask], source_vertices[~leaf_mask]):
        raise ValueError("trunk positions changed")
    vertices = (vertices @ CONVERSION.T).astype(np.float32)
    areas = np.linalg.norm(np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]],
                                   vertices[faces[:, 2]] - vertices[faces[:, 0]]), axis=1) / 2
    if np.any(areas[leaf_faces] <= 0) or not np.all(np.isfinite(vertices)):
        raise ValueError("source reference cards did not produce valid triangles")
    if len(np.unique(np.sort(faces, axis=1), axis=0)) != len(faces):
        raise ValueError("unexpected duplicate indexed triangles")
    used = sorted(set(map(int, original["material_ids"])))
    slots = [material_id(metadata["materials"][i]) for i in used]
    if slots != spec["material_slots"]:
        raise ValueError("stable source material slots differ")
    material_ids = np.array([used.index(int(i)) for i in original["material_ids"]], dtype=np.int32)
    normals = original["normals"] @ np.linalg.inv(CONVERSION)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 1e-8)
    uv3 = delta[:, :2].copy()
    uv3[:, 1] = 1 - uv3[:, 1]
    uv4 = delta[:, 2:4].copy()
    uv4[:, 1] = 1 - uv4[:, 1]
    uvs = [original["uv0"], original["uv1"], np.zeros((len(vertices), 2), dtype=np.float32), uv3, uv4]
    reconstructed_delta = np.column_stack((uv3[:, 0], 1 - uv3[:, 1], uv4[:, 0], 1 - uv4[:, 1]))
    raw_delta_error = float(np.abs(reconstructed_delta - delta).max())
    centers = source_vertices[leaf_mask].astype(np.float64) @ CONVERSION.T
    radii = np.linalg.norm(delta[leaf_mask, :3].astype(np.float64), axis=1, keepdims=True)
    billboard = bounds(np.concatenate((vertices, centers - radii, centers + radii)))
    result = dict(vertices=vertices, faces=faces.astype(np.int32), normals=normals,
                  uvs=uvs, material_ids=material_ids, slots=slots)
    evidence = dict(original_npz_position_error_m=0., trunk_positions_exact=True,
                    original_uv0_exact=True, source_normals_preserved=True,
                    source_pivot_bounds_ros_m=bounds(source_vertices @ CONVERSION.T),
                    reference_bounds_dagor_m=bounds(vertices.astype(np.float64) @ CONVERSION),
                    reference_bounds_ros_m=bounds(vertices), billboard_bounds_ros_m=billboard,
                    minimum_leaf_triangle_area_m2=float(areas[leaf_faces].min()),
                    leaf_triangles=int(leaf_faces.sum()), source_winding_corrections=int(flip.sum()),
                    ue_vflip_reconstructed_raw_delta_max_error=raw_delta_error)
    return result, evidence


def export(path, identifier, data):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mesh = bpy.data.meshes.new(identifier)
    vertices, faces = data["vertices"], data["faces"]
    mesh.vertices.add(len(vertices))
    mesh.vertices.foreach_set("co", vertices.ravel())
    mesh.loops.add(faces.size)
    mesh.loops.foreach_set("vertex_index", faces.ravel())
    mesh.polygons.add(len(faces))
    mesh.polygons.foreach_set("loop_start", np.arange(len(faces), dtype=np.int32) * 3)
    mesh.polygons.foreach_set("loop_total", np.full(len(faces), 3, dtype=np.int32))
    for slot in data["slots"]:
        mesh.materials.append(bpy.data.materials.new(slot))
    mesh.polygons.foreach_set("material_index", data["material_ids"])
    for index, uv in enumerate(data["uvs"]):
        layer = mesh.uv_layers.new(name="UV" + str(index))
        layer.data.foreach_set("uv", uv[faces.ravel()].astype(np.float32).ravel())
    mesh.update()
    mesh.normals_split_custom_set_from_vertices(data["normals"].tolist())
    obj = bpy.data.objects.new(identifier, mesh)
    bpy.context.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.fbx(
        filepath=str(path), use_selection=True, object_types={"MESH"},
        axis_forward="X", axis_up="Z", apply_unit_scale=True, apply_scale_options="FBX_SCALE_UNITS",
        use_mesh_modifiers=True, mesh_smooth_type="OFF", add_leaf_bones=False,
        bake_anim=False, path_mode="AUTO")


def verify_roundtrip(path, data):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(path))
    objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if len(objects) != 1:
        raise ValueError("FBX object count changed")
    obj = objects[0]
    mesh = obj.data
    mesh.calc_loop_triangles()
    if len(mesh.vertices) != len(data["vertices"]) or len(mesh.loop_triangles) != len(data["faces"]):
        raise ValueError("FBX vertex/triangle count changed")
    if [layer.name for layer in mesh.uv_layers] != ["UV" + str(i) for i in range(5)]:
        raise ValueError("FBX UV channel order changed")
    if [material.name for material in mesh.materials] != data["slots"]:
        raise ValueError("FBX material order changed")
    actual_points = np.asarray([obj.matrix_world @ vertex.co for vertex in mesh.vertices], dtype=np.float64)
    actual_bounds = bounds(actual_points)
    bounds_error = max(float(np.abs(np.asarray(actual_bounds[key]) -
                                   np.asarray(bounds(data["vertices"])[key])).max()) for key in ("lo", "hi"))
    if bounds_error > 0.00003:
        raise ValueError("FBX reference bounds mismatch")
    expected_corner = np.column_stack([data["vertices"], *data["uvs"]])[data["faces"]]
    actual_corner = []
    actual_material = []
    for triangle in mesh.loop_triangles:
        rows = []
        for loop_id in triangle.loops:
            vertex_id = mesh.loops[loop_id].vertex_index
            rows.append([*actual_points[vertex_id],
                         *(value for layer in mesh.uv_layers for value in layer.data[loop_id].uv)])
        actual_corner.append(rows)
        actual_material.append(triangle.material_index)
    # Match each face up to cyclic order, checking geometry and all UV attributes.
    # This accepts FBX face reordering while still verifying the original topology.
    rotations = np.stack([np.roll(expected_corner, shift, axis=1) for shift in range(3)], axis=1)
    tolerance = np.array([0.00003] * 3 + [0.000001] * 10)
    remaining = np.ones(len(data["faces"]), dtype=bool)
    max_position_error = max_uv_error = 0.
    for actual, material in zip(np.asarray(actual_corner), actual_material):
        candidates = np.flatnonzero(remaining & (data["material_ids"] == material))
        if not len(candidates):
            raise ValueError("FBX face material assignment changed")
        differences = np.abs(rotations[candidates] - actual)
        errors = np.max(differences / tolerance, axis=(2, 3))
        best = np.unravel_index(int(np.argmin(errors)), errors.shape)
        if errors[best] > 1:
            raise ValueError("FBX topology/UV payload changed, normalized error " + str(errors[best]))
        matched = differences[best]
        max_position_error = max(max_position_error, float(matched[:, :3].max()))
        max_uv_error = max(max_uv_error, float(matched[:, 3:].max()))
        remaining[candidates[best[0]]] = False
    return dict(status="passed", vertices=len(mesh.vertices), triangles=len(mesh.loop_triangles),
                uv_layers=[layer.name for layer in mesh.uv_layers], bounds_m=actual_bounds,
                bounds_error_m=bounds_error, corner_position_error_m=max_position_error,
                all_uv_corner_error=max_uv_error, topology_materials_and_uv0_to_uv4_verified=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--payload-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    output, evidence = args.output.resolve(), args.evidence_root.resolve()
    manifest_path = output / "scene-source.json"
    manifest = read(manifest_path)
    old_manifest = copy.deepcopy(manifest)
    old_manifest_hash = sha(manifest_path)
    history_path = output / "preparation-verification.json"
    history_hash = sha(history_path)
    raw_report_path = args.payload_root / "raw-payload-verification.json"
    raw_report = read(raw_report_path)
    if manifest["id"] != "wt_vlaanderen" or manifest["source_sha256"] != raw_report["source_level_sha256"]:
        raise ValueError("source scene identity differs")
    if raw_report["status"] != "passed":
        raise ValueError("raw source payload verification is incomplete")
    independent_path = evidence / "independent-source-verification.json"
    independent = read(independent_path)
    if independent["status"] != "passed" or independent["raw_verification_sha256"] != sha(raw_report_path):
        raise ValueError("independent raw/source-placement verification is incomplete or stale")
    raw_models = {record["name"]: record for record in raw_report["models"]}
    materials = {material["id"]: material for material in manifest["materials"]}
    affected = [spec for spec in manifest["meshes"] if any(
        materials[slot]["shader"] == "rendinst_facing_leaves" for slot in spec["material_slots"])]
    if {spec["source"] for spec in affected} != set(MODELS) or len(affected) != 3:
        raise ValueError("facing-leaf coverage changed; affine/model handling requires review")
    prepared = []
    for spec in affected:
        name = spec["source"]
        expected_vertices, expected_triangles, quads, instances = MODELS[name]
        raw_record = raw_models[name]
        folder = args.source_root / "models-all-07" / name
        metadata = read(folder / "model.json")
        payload_path = args.payload_root / raw_record["payload_file"]
        if sha(folder / "mesh.npz") != raw_record["source_mesh_sha256"]:
            raise ValueError("source NPZ changed")
        if sha(folder / "model.json") != raw_record["source_model_json_sha256"]:
            raise ValueError("source metadata changed")
        if sha(payload_path) != raw_record["payload_sha256"]:
            raise ValueError("raw payload changed")
        if sha(output / spec["file"]) != spec["sha256"]:
            raise ValueError("pre-adaptation FBX hash differs")
        with np.load(folder / "mesh.npz") as archive:
            original = {key: archive[key].copy() for key in archive.files}
        with np.load(payload_path) as archive:
            payload = {key: archive[key].copy() for key in archive.files}
        data, geometry_evidence = prepare(name, original, payload, metadata, spec)
        if (len(data["vertices"]), len(data["faces"]), int(payload["leaf_face_mask"].sum())) != (
                expected_vertices, expected_triangles, quads * 2):
            raise ValueError("original model counts changed")
        rmi_path = output / spec["instances_file"]
        raw_rmi = rmi_path.read_bytes()
        rows = np.frombuffer(raw_rmi[8:], dtype="<f4").reshape(-1, 10)
        if raw_rmi[:4] != b"RMI1" or struct.unpack_from("<I", raw_rmi, 4)[0] != instances or len(rows) != instances:
            raise ValueError("instance stream count changed")
        scales = rows[:, 7:]
        if not np.all(np.isfinite(rows)) or np.any(scales <= 0) or np.any(np.ptp(scales, axis=1) != 0):
            raise ValueError("camera_no_wind adapter needs positive uniform instance scale")
        record = dict(raw_record, mesh_id=spec["id"], before_fbx_sha256=spec["sha256"],
                      before_reference_bounds_m=spec["bounds_m"], instances_sha256=sha(rmi_path),
                      instance_count=len(rows), minimum_uniform_scale=float(scales.min()),
                      maximum_uniform_scale=float(scales.max()), maximum_scale_anisotropy=0.,
                      geometry=geometry_evidence)
        prepared.append((spec, data, record))
    evidence.mkdir(parents=True, exist_ok=True)
    before = evidence / "before"
    before.mkdir(exist_ok=False)
    staging = evidence / "staging"
    staging.mkdir(exist_ok=False)
    for path in [manifest_path, history_path, *[output / spec["file"] for spec in affected]]:
        destination = before / path.relative_to(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        if sha(destination) != sha(path):
            raise ValueError("backup verification failed")
    records = []
    for spec, data, record in prepared:
        target = staging / Path(spec["file"]).name
        export(target, spec["id"], data)
        roundtrip = verify_roundtrip(target, data)
        record.update(after_fbx_sha256=sha(target), backup_file=str(before / spec["file"]),
                      output_file=str(output / spec["file"]), roundtrip=roundtrip)
        spec.update(sha256=sha(target), bounds_m=bounds(data["vertices"]),
                    billboard_bounds_m=record["geometry"]["billboard_bounds_ros_m"],
                    leaf_adapter=copy.deepcopy(CONTRACT))
        records.append(record)
    # All three stage files passed before replacing any active source package file.
    affected_ids = {spec["id"] for spec in affected}
    compare_manifest = copy.deepcopy(manifest)
    compare_manifest["meshes"] = [next(item for item in old_manifest["meshes"] if item["id"] == spec["id"])
                                  if spec["id"] in affected_ids else spec for spec in manifest["meshes"]]
    if compare_manifest != old_manifest:
        raise ValueError("unexpected scene-source change outside three mesh records")
    for independent_model in independent["models"]:
        if sha(output / independent_model["rmi_file"]) != independent_model["rmi_sha256"]:
            raise ValueError("RMI placement changed since independent source audit")
    if sha(manifest_path) != old_manifest_hash or sha(history_path) != history_hash:
        raise ValueError("source package changed while preparing leaf adaptation")
    for spec in affected:
        original_spec = next(item for item in old_manifest["meshes"] if item["id"] == spec["id"])
        if sha(output / spec["file"]) != original_spec["sha256"]:
            raise ValueError("active FBX changed while preparing")
    for spec in affected:
        shutil.copy2(staging / Path(spec["file"]).name, output / spec["file"])
    pending_manifest = staging / "scene-source.json"
    save(pending_manifest, manifest)
    pending_manifest.replace(manifest_path)
    report = dict(
        status="passed",
        scope="three sourced facing-leaf FBX reference cards and raw UV payloads; UE import/rendering pending",
        blender_version=bpy.app.version_string, adapter=CONTRACT,
        preparation_script_sha256=sha(Path(__file__)),
        independent_source_verification=str(independent_path),
        independent_source_verification_sha256=sha(independent_path),
        source_level_sha256=raw_report["source_level_sha256"],
        source_groups=raw_report["source_groups"], dae_commit=raw_report["dae_commit"],
        raw_payload_verification=str(raw_report_path), raw_payload_verification_sha256=sha(raw_report_path),
        before_scene_source_sha256=old_manifest_hash, after_scene_source_sha256=sha(manifest_path),
        original_preparation_verification_sha256=history_hash,
        original_preparation_verification_unchanged=sha(history_path) == history_hash,
        backup_root=str(before), total_leaf_quads=sum(record["leaf_quads"] for record in records),
        total_leaf_triangles=sum(record["leaf_triangles"] for record in records),
        total_original_instances=sum(record["instance_count"] for record in records),
        affine_leaf_meshes=0, all_leaf_instances_uniform_positive_scale=True,
        untouched_manifest_visual_mesh_records=len(manifest["meshes"]) - len(affected),
        untouched_manifest_collision_mesh_records=len(manifest["collision_meshes"]),
        source_limits_unchanged=manifest["limitations"] == old_manifest["limitations"],
        models=records,
    )
    if not report["original_preparation_verification_unchanged"]:
        raise ValueError("historical preparation report changed")
    save(evidence / "verification.json", report)
    print("WT_LEAF_ADAPTATION_VERIFIED", json.dumps({
        key: report[key] for key in ("status", "total_leaf_quads", "total_leaf_triangles",
                                    "total_original_instances", "after_scene_source_sha256")}), flush=True)


if __name__ == "__main__":
    main()
