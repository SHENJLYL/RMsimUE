"""Independently audit saved leaf payload bytes against source NPZ and RMI placement."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import numpy as np

C = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=np.float64)


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--payload-root", type=Path, required=True)
    p.add_argument("--scene-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    report_path = a.payload_root / "raw-payload-verification.json"
    extraction = read(report_path)
    scene = read(a.scene_root / "scene-source.json")
    names = {m["name"] for m in extraction["models"]}
    materials = {m["id"]: m for m in scene["materials"]}
    affected = [m for m in scene["meshes"]
                if any(materials[s]["shader"] == "rendinst_facing_leaves" for s in m["material_slots"])]
    if {m["source"] for m in affected} != names or len(affected) != 3:
        raise ValueError("unexpected leaf mesh / affine coverage")
    layouts = {}
    layout_files = {}
    for path in sorted((Path(extraction["source_root"]) / "layout-06").glob("layer-*-instances.json")):
        for name, entry in read(path).items():
            if name in names:
                if name in layouts:
                    raise ValueError("unexpected duplicated source layer")
                layouts[name] = entry
                layout_files[name] = dict(path=str(path), sha256=sha(path))
    for group, expected in extraction["source_groups"].items():
        if sha(Path(group)) != expected:
            raise ValueError("source GRP changed")
    records = []
    for record in extraction["models"]:
        name = record["name"]
        path = a.payload_root / record["payload_file"]
        if sha(path) != record["payload_sha256"]:
            raise ValueError("payload hash differs")
        if sha(Path(record["source_mesh_npz"])) != record["source_mesh_sha256"]:
            raise ValueError("original source model changed")
        with np.load(path) as f:
            data = {k: f[k].copy() for k in f.files}
        with np.load(record["source_mesh_npz"]) as f:
            src = {k: f[k].copy() for k in f.files}
        cursor = 0
        leaf_raw_bytes = 0
        for draw_index, draw in enumerate(record["draws"]):
            raw = data["raw_draw_" + str(draw_index)].tobytes()
            n = draw["vertices"]
            if hashlib.sha256(raw).hexdigest() != draw["raw_sha256"]:
                raise ValueError("draw bytes changed")
            if draw["leaf"]:
                if draw["stride"] != 24:
                    raise ValueError("unexpected source stride")
                # Independent direct byte interpretation; no Dagor decoder functions.
                pos = np.ndarray((n, 4), "<f2", raw, 0, (24, 2)).astype(np.float32)[:, :3]
                delta = np.ndarray((n, 4), "<f2", raw, 12, (24, 2)).astype(np.float32)
                normal = np.ndarray((n, 4), "u1", raw, 20, (24, 1)).copy()
                uv = np.ndarray((n, 2), "<i2", raw, 8, (24, 2)).astype(np.float32) / 4096
                uv[:, 1] = 1 - uv[:, 1]
                span = slice(cursor, cursor + n)
                if not (np.array_equal(pos, src["vertices"][span]) and
                        np.array_equal(delta, data["delta_xyzw"][span]) and
                        np.array_equal(normal, data["packed_normal_bgra"][span]) and
                        np.array_equal(uv, src["uv0"][span]) and np.all(data["leaf_mask"][span])):
                    raise ValueError("independent source byte audit differs: " + name)
                leaf_raw_bytes += len(raw)
            cursor += n
        if cursor != len(src["vertices"]) or not np.array_equal(data["positions"], src["vertices"]):
            raise ValueError("position order differs")
        mask = data["leaf_mask"]
        expanded = src["vertices"].astype(np.float64).copy()
        expanded[mask] += data["delta_xyzw"][mask, :3] * [1, 1, -1]
        leaf_faces = src["triangles"][data["leaf_face_mask"]]
        areas = np.linalg.norm(np.cross(expanded[leaf_faces[:, 1]] - expanded[leaf_faces[:, 0]],
                                        expanded[leaf_faces[:, 2]] - expanded[leaf_faces[:, 0]]), axis=1) / 2
        if not np.all(areas > 0):
            raise ValueError("expanded source leaf still has a zero-area triangle")
        if not np.array_equal(expanded[~mask], src["vertices"][~mask]):
            raise ValueError("trunk positions changed")
        m = next(m for m in affected if m["source"] == name)
        raw = (a.scene_root / m["instances_file"]).read_bytes()
        rows = np.frombuffer(raw[8:], "<f4").reshape(-1, 10)
        if raw[:4] != b"RMI1" or struct.unpack_from("<I", raw, 4)[0] != len(rows):
            raise ValueError("invalid RMI")
        original = layouts[name]
        if not (original["pos_inst"] and original["palette_rotation"] and original["palette_rotation_count"] == 1):
            raise ValueError("this adapter requires source uniform-scale position instances")
        placements = np.asarray(original["instances"], dtype=np.float64)
        words = placements[:, 3].astype(np.int64)
        if np.any(words & 7):
            raise ValueError("unexpected source rotation")
        scale = (words & ~7) / 256.
        expected = np.zeros_like(rows)
        expected[:, :3] = placements[:, :3] @ C.T
        expected[:, 6] = 1
        expected[:, 7:] = scale[:, None]
        if not np.array_equal(rows, expected) or np.any(scale <= 0):
            raise ValueError("source instances / serialized placement disagree")
        if len(rows) != record["original_instances"]:
            raise ValueError("source instance count differs")
        records.append(dict(name=name, raw_leaf_bytes=leaf_raw_bytes, raw_delta4d_exact=True,
                            packed_normal_bytes_exact=True, original_uv0_exact=True,
                            original_npz_position_error_m=0., unchanged_trunk_vertices=True,
                            expanded_leaf_triangle_min_area_m2=float(areas.min()),
                            leaf_quads=record["leaf_quads"], leaf_triangles=len(leaf_faces),
                            source_layout=layout_files[name], original_instances=len(rows),
                            rmi_file=m["instances_file"], rmi_sha256=sha(a.scene_root / m["instances_file"]),
                            serialized_instance_rows_exact=True, uniform_scale_min=float(scale.min()),
                            uniform_scale_max=float(scale.max()), max_scale_anisotropy=0.))
    result = dict(status="passed", scope="independent raw-byte/source-placement audit; no UE rendering",
                  raw_verification_sha256=sha(report_path), source_groups=extraction["source_groups"],
                  affine_meshes_using_leaf_materials=0, total_leaf_quads=sum(r["leaf_quads"] for r in records),
                  total_leaf_triangles=sum(r["leaf_triangles"] for r in records),
                  total_original_instances=sum(r["original_instances"] for r in records), models=records)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    if a.output.exists():
        raise FileExistsError(a.output)
    a.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("WT_LEAF_INDEPENDENT_VERIFIED", json.dumps({k: result[k] for k in
          ("status", "total_leaf_quads", "total_leaf_triangles", "total_original_instances")}), flush=True)


if __name__ == "__main__":
    main()
