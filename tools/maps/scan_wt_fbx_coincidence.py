"""Scan prepared collision/world FBXs for exactly coincident polygon corners.

Uses Blender's binary FBX parser directly, without importing a Blender Mesh.
The known world_decal_000 has a separate source proof and is excluded here.
No source FBX, manifest or UE asset is changed. Detailed indices stay local.
"""
import argparse
import datetime
import gc
import hashlib
import json
from pathlib import Path
import sys
import traceback

import bpy
import numpy as np
from io_scene_fbx import parse_fbx


def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def child(node, identity):
    found = [value for value in node.elems if value.id == identity]
    if len(found) != 1:
        raise ValueError("expected one FBX element " + repr(identity))
    return found[0]


def memory():
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    get_process = ctypes.windll.kernel32.GetCurrentProcess
    get_process.restype = wintypes.HANDLE
    query = ctypes.windll.psapi.GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    query.restype = wintypes.BOOL
    if not query(get_process(), ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return dict(working_set_bytes=counters.WorkingSetSize,
                peak_working_set_bytes=counters.PeakWorkingSetSize,
                private_bytes=counters.PagefileUsage,
                peak_private_bytes=counters.PeakPagefileUsage)


def scan_mesh(package, specification, category, detail_directory):
    path = (package / specification["file"]).resolve(strict=True)
    if not path.is_relative_to(package):
        raise ValueError("FBX path outside package")
    before = sha(path)
    if before != specification["sha256"]:
        raise ValueError("FBX differs from manifest: " + specification["id"])
    root, version = parse_fbx.parse(str(path))
    geometries = [node for node in child(root, b"Objects").elems
                  if node.id == b"Geometry" and node.props[2] == b"Mesh"]
    if len(geometries) != 1:
        raise ValueError("expected exactly one baked source mesh: " + specification["id"])
    geometry = geometries[0]
    vertices = np.asarray(child(geometry, b"Vertices").props[0], dtype=np.float64).reshape(-1, 3)
    encoded = np.asarray(child(geometry, b"PolygonVertexIndex").props[0])
    if encoded.dtype.kind not in "iu" or not np.all(np.isfinite(vertices)):
        raise ValueError("invalid FBX numeric data")
    ends = np.flatnonzero(encoded < 0)
    if not len(ends) or ends[-1] != len(encoded) - 1 or not np.all(np.diff(np.r_[-1, ends]) == 3):
        raise ValueError("FBX must contain complete triangular polygons")
    triangles = encoded.astype(np.int64, copy=True)
    negative = triangles < 0
    triangles[negative] = -triangles[negative] - 1
    triangles = triangles.reshape(-1, 3)
    if np.any(triangles < 0) or np.any(triangles >= len(vertices)):
        raise ValueError("FBX polygon references an absent vertex")
    if len(triangles) != specification["triangles"] or len(vertices) != specification["vertices"]:
        raise ValueError("binary FBX counts differ from manifest: " + specification["id"])
    indices = []
    for start in range(0, len(triangles), 32768):
        points = vertices[triangles[start:start + 32768]]
        pairs = (np.all(points[:, 0] == points[:, 1], axis=1)
                 | np.all(points[:, 0] == points[:, 2], axis=1)
                 | np.all(points[:, 1] == points[:, 2], axis=1))
        indices.extend((np.flatnonzero(pairs) + start).tolist())
        del points, pairs
    record = dict(id=specification["id"], category=category, file=specification["file"],
                  fbx_sha256=before, fbx_version=int(version), vertices=len(vertices),
                  source_triangles=specification["triangles"], fbx_triangles=len(triangles),
                  strict_coincident=len(indices), bytes=path.stat().st_size)
    if indices:
        selected = np.asarray(indices, dtype=np.int64)
        detail = detail_directory / (specification["id"] + ".npz")
        np.savez_compressed(detail, binary_fbx_polygon_indices=selected,
                            original_vertex_indices=triangles[selected],
                            raw_fbx_corner_positions=vertices[triangles[selected]])
        record.update(detail_file="details/" + detail.name, detail_sha256=sha(detail),
                      first_polygon_indices=indices[:12])
        print("WT_FBX_COINCIDENT_FOUND " + json.dumps(record), flush=True)
    if sha(path) != before:
        raise ValueError("FBX changed during scan: " + specification["id"])
    # Parser tree/array references remain local and are released before the next file.
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene-source", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    scene_file = args.scene_source.resolve(strict=True)
    package = scene_file.parent
    source_bytes = scene_file.read_bytes()
    source = json.loads(source_bytes.decode("utf-8-sig"))
    if source.get("id") != "wt_vlaanderen":
        raise ValueError("expected original Flanders package")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    details = output / "details"
    details.mkdir()
    world = [item for item in source["meshes"] if item["id"].startswith("world_")]
    excluded = [item for item in world if item["id"] == "world_decal_000"]
    targets = [("visual_world", item) for item in world if item["id"] != "world_decal_000"]
    targets += [("collision", item) for item in source["collision_meshes"]]
    report = dict(schema="wt_binary_fbx_coincident_scan_v1", status="running", complete=False,
                  scope="exact pairwise coincidence in binary FBX polygon/control-point arrays",
                  native_unreal_verified=False, source_geometry_modified=False,
                  blender_version=bpy.app.version_string, argv=sys.argv,
                  audit_script_sha256=sha(Path(__file__).resolve()),
                  parser_file=str(Path(parse_fbx.__file__)), parser_sha256=sha(Path(parse_fbx.__file__)),
                  source_manifest=str(scene_file),
                  source_manifest_sha256=hashlib.sha256(source_bytes).hexdigest(),
                  started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  excluded_already_audited=[dict(id=item["id"], fbx_sha256=item["sha256"],
                                               reason="dedicated original-NPZ/FBX/native-target proof")
                                             for item in excluded],
                  expected_collision_files=len(source["collision_meshes"]),
                  expected_world_files=len(world) - len(excluded), scanned_files=0,
                  scanned_triangles=0, strict_coincident=0, suspicious_meshes=[],
                  counts_by_category={label: dict(files=0, triangles=0, strict_coincident=0)
                                      for label in ("visual_world", "collision")})
    inventory = output / "files.jsonl"
    failure = None
    save(output / "verification.json", report)
    try:
        with inventory.open("x", encoding="utf-8") as log:
            for category, specification in targets:
                record = scan_mesh(package, specification, category, details)
                log.write(json.dumps(record, allow_nan=False) + "\n")
                log.flush()
                report["scanned_files"] += 1
                report["scanned_triangles"] += record["fbx_triangles"]
                report["strict_coincident"] += record["strict_coincident"]
                totals = report["counts_by_category"][category]
                totals["files"] += 1
                totals["triangles"] += record["fbx_triangles"]
                totals["strict_coincident"] += record["strict_coincident"]
                if record["strict_coincident"]:
                    report["suspicious_meshes"].append(record)
                del record
                gc.collect()
                if report["scanned_files"] % 50 == 0:
                    report["memory"] = memory()
                    save(output / "verification.json", report)
                    print("WT_FBX_SCAN_PROGRESS " + str(report["scanned_files"]) + "/" + str(len(targets)),
                          flush=True)
        report.update(status="passed", complete=True, files_inventory_sha256=sha(inventory),
                      source_manifest_sha256_after=sha(scene_file), memory=memory())
    except Exception:
        failure = traceback.format_exc()
        report.update(status="failed", complete=True, error=failure, memory=memory())
    finally:
        report["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(output / "verification.json", report)
    print("WT_FBX_SCAN_COMPLETE " + json.dumps({
        key: report[key] for key in ("status", "scanned_files", "scanned_triangles",
                                    "strict_coincident", "counts_by_category", "suspicious_meshes", "memory")
        if key in report}), flush=True)
    if failure:
        raise RuntimeError(failure)


if __name__ == "__main__":
    main()
