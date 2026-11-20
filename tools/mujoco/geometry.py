"""Source-bound geometry for the native MuJoCo backend. No visual asset is changed."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import struct
import numpy as np

SOURCE_STEP = "a5ad3ede3b7674fc98f2e73b71717216383cb2b4b5dcdbe900c2038918bea8e0"

def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def read_glb(path):
    raw = Path(path).read_bytes()
    magic, version, total = struct.unpack_from("<4sII", raw)
    if (magic, version, total) != (b"glTF", 2, len(raw)):
        raise ValueError("Invalid source GLB header")
    length, kind = struct.unpack_from("<II", raw, 12)
    if kind != 0x4e4f534a:
        raise ValueError("GLB JSON chunk missing")
    doc = json.loads(raw[20:20+length])
    blen, bkind = struct.unpack_from("<II", raw, 20+length)
    if bkind != 0x004e4942 or doc.get("extensionsRequired"):
        raise ValueError("Unsupported GLB buffers/extensions")
    if doc["asset"].get("extras", {}).get("source_sha256") != SOURCE_STEP:
        raise ValueError("GLB does not declare the pinned RMUC source STEP")
    return doc, memoryview(raw)[28+length:28+length+blen]

def accessor(doc, blob, index):
    value = doc["accessors"][index]
    view = doc["bufferViews"][value["bufferView"]]
    if "sparse" in value or view.get("buffer", 0) != 0:
        raise ValueError("Unsupported sparse/external accessor")
    dtype = {5123:"<u2", 5125:"<u4", 5126:"<f4"}[value["componentType"]]
    width = {"SCALAR":1, "VEC3":3}[value["type"]]
    item = np.dtype(dtype).itemsize
    return np.ndarray((value["count"], width), dtype=dtype, buffer=blob,
        offset=view.get("byteOffset", 0)+value.get("byteOffset", 0),
        strides=(view.get("byteStride", width*item), item))

def world_primitives(doc, blob):
    def visit(index, parent):
        node = doc["nodes"][index]
        if any(key in node for key in ("rotation", "translation", "scale")):
            raise ValueError("Expected source matrix transforms")
        transform = parent @ np.asarray(node.get("matrix", np.eye(4).ravel(order="F")),
            dtype=float).reshape(4, 4, order="F")
        if "mesh" in node:
            match = re.fullmatch(r"(.+)_\d+_source_.+", node["name"])
            if not match:
                raise ValueError("Unrecognized source grouping: "+node["name"])
            group = match[1].replace("-", "_")
            for primitive in doc["meshes"][node["mesh"]]["primitives"]:
                if primitive.get("mode", 4) != 4:
                    raise ValueError("Source mesh is not triangulated")
                points = accessor(doc, blob, primitive["attributes"]["POSITION"])
                points = points @ transform[:3,:3].T + transform[:3,3]
                faces = accessor(doc, blob, primitive["indices"]).reshape(-1,3).astype(np.int32)
                if np.linalg.det(transform[:3,:3]) < 0:
                    faces = faces[:,[0,2,1]]
                yield group, node["name"], points, faces
        for child in node.get("children", []):
            yield from visit(child, transform)
    for index in doc["scenes"][doc.get("scene",0)]["nodes"]:
        yield from visit(index, np.eye(4))
        
def exact_cleanup(points, faces):
    # np.unique changes indexing only. No vertex welding tolerance or rounding.
    points, inverse = np.unique(np.asarray(points, dtype=np.float64), axis=0, return_inverse=True)
    faces = inverse[np.asarray(faces, dtype=np.int64)].astype(np.int32)
    if not np.isfinite(points).all():
        raise ValueError("Nonfinite source geometry")
    cross = np.cross(points[faces[:,1]]-points[faces[:,0]], points[faces[:,2]]-points[faces[:,0]])
    valid = np.any(cross != 0, axis=1)
    removed_zero = int((~valid).sum())
    faces = faces[valid]
    canonical = np.sort(faces, axis=1)
    _, first = np.unique(canonical, axis=0, return_index=True)
    removed_duplicate = int(len(faces)-len(first))
    faces = faces[np.sort(first)]
    used, inv = np.unique(faces.ravel(), return_inverse=True)
    return points[used], inv.reshape(-1,3).astype(np.int32), {
        "exact_zero_area_faces_removed": removed_zero,
        "exact_duplicate_faces_removed": removed_duplicate,
        "vertex_weld_tolerance_m": 0.0}

def extract_rmuc(repo, output):
    repo, output = Path(repo), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    source = repo/"artifacts/sources/rmuc2027/optimized/rmuc2027/full-map-collision.glb"
    metadata_path = repo/"assets/maps/rmuc2027/mesh-source.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    doc, blob = read_glb(source)
    translation = np.asarray(metadata["source_to_sim_translation_m"])
    groups = {}
    for name, source_node, vertices, faces in world_primitives(doc, blob):
        groups.setdefault(name, []).append((source_node, vertices+translation, faces))
    expected = {item["id"]:item for item in metadata["collisions"]}
    if set(groups) != set(expected):
        raise ValueError("Original collision group identity mismatch")
    report = {"schema":"rmsim_mujoco_static_geometry_v1", "source":str(source.relative_to(repo)).replace("\\","/"),
        "source_sha256":digest(source), "mesh_metadata_sha256":digest(metadata_path),
        "coordinate_system":"ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP", "units":"m", "groups":[],
        "method":"source_float32_export_then_exact_vertex_and_face_deduplication"}
    for name, pieces in groups.items():
        vertices, faces, offset = [], [], 0
        for _, point, face in pieces:
            vertices.append(point)
            faces.append(face+offset)
            offset += len(point)
        points = np.concatenate(vertices).astype(np.float32).astype(np.float64)
        triangles = np.concatenate(faces).astype(np.int32)
        if len(triangles) != expected[name]["triangles"]:
            raise ValueError("Source face count changed: "+name)
        bounds = {"lo":points.min(axis=0).tolist(), "hi":points.max(axis=0).tolist()}
        if any(abs(bounds[k][i]-expected[name]["bounds_m"][k][i])>1e-6 for k in ("lo","hi") for i in range(3)):
            raise ValueError("Source bounds mismatch: "+name)
        original_faces, original_vertices = len(triangles),len(points)
        points,triangles,cleanup = exact_cleanup(points,triangles)
        path = output/(name+".npz")
        np.savez_compressed(path, vertices=points, triangles=triangles)
        item = {"id":name, "file":path.name, "sha256":digest(path), "source_nodes":sorted(set(p[0] for p in pieces)),
            "source_triangles":original_faces, "source_vertices":original_vertices,
            "vertices":len(points), "triangles":len(triangles), "bounds_m":bounds, **cleanup}
        report["groups"].append(item)
        print(json.dumps({"geometry":name,"source_triangles":original_faces,
            "triangles":len(triangles),"vertices":len(points)},ensure_ascii=False),flush=True)
    report["total_triangles"] = sum(x["triangles"] for x in report["groups"])
    report["total_vertices"] = sum(x["vertices"] for x in report["groups"])
    (output/"geometry.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    return report

def load_geometry(directory, repo):
    directory,repo = Path(directory),Path(repo)
    report = json.loads((directory/"geometry.json").read_text(encoding="utf-8-sig"))
    if report["schema"] != "rmsim_mujoco_static_geometry_v1":
        raise ValueError("Unsupported geometry manifest")
    if digest(repo/report["source"]) != report["source_sha256"]:
        raise ValueError("Original collision source hash changed")
    if digest(repo/"assets/maps/rmuc2027/mesh-source.json") != report["mesh_metadata_sha256"]:
        raise ValueError("Original collision coordinate metadata changed")
    expected_ids={m["id"] for m in json.loads((repo/"assets/maps/rmuc2027/map.json").read_text())["meshes"]}
    if {i["id"] for i in report["groups"]} != expected_ids:
        raise ValueError("Static geometry does not cover the complete active RMUC map")
    if report.get("status") == "pending_independent_surface_verification":
        raise ValueError("Unverified geometry candidate cannot be used by the simulator")
    groups = []
    for item in report["groups"]:
        item=dict(item)
        file = directory/item["file"]
        if file.parent != directory or digest(file) != item["sha256"]:
            raise ValueError("Geometry payload identity changed")
        with np.load(file,allow_pickle=False) as mesh:
            points,triangles = mesh["vertices"].copy(),mesh["triangles"].copy()
        if points.shape != (item["vertices"],3) or triangles.shape != (item["triangles"],3):
            raise ValueError("Geometry count mismatch")
        if item.get("convex_file"):
            file=directory/item["convex_file"]
            if file.parent!=directory or digest(file)!=item["convex_sha256"]:
                raise ValueError("Convex payload identity changed")
            with np.load(file,allow_pickle=False) as mesh:
                hv,offsets=mesh["vertices"].copy(),mesh["offsets"].copy()
            if len(offsets)!=item["convex_count"]+1 or offsets[0]!=0 or offsets[-1]!=len(hv):
                raise ValueError("Invalid convex payload offsets")
            item["_convex_hulls"]=[hv[offsets[i]:offsets[i+1]] for i in range(len(offsets)-1)]
        groups.append((item,points,triangles))
    return report,groups

if __name__ == "__main__":
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("--repo",type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    extract_rmuc(a.repo,a.output)
