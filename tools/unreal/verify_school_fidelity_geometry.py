"""Strict OBJ -> StaticMesh LODResources[0] triangle audit for SchoolGymFidelity.

No Unreal process is launched here. Call verify_static_mesh_against_obj after
the importer has finished asset compilation and final build settings.

The native extraction is the enabled ProceduralMeshComponent plugin's
ProceduralMeshLibrary.get_section_from_static_mesh: actual LODResources
positions, indices and UV0, not just imported MeshDescription or triangle count.
For Nanite-enabled assets this buffer is the fallback representation. Nanite
cluster geometry is a separate representation and is not extracted by this API.
A source/fallback mismatch is scoped to that fallback, not proof of cluster loss.

The OBJ transport convention is explicit: source UE metres are reflected in Y
and face corners reversed before the legacy FBX importer, which converts Y back.
UV0 follows FbxStaticMeshImport.cpp: float(u), 1.f - float(v).
Unknown missing/extra triangles, wrong UV/material/winding fail. No percentage
triangle tolerance exists. At most two float32 ULPs permit scalar rounding;
they never excuse a missing triangle.
"""
from collections import defaultdict
from pathlib import Path
from itertools import product
import hashlib
import json
import math
import struct

ASSET_PREFIX = "/Game/RMSim/Maps/rmuc2027/Arena/SchoolGymFidelity/"
ENGINE_SOURCE_EVIDENCE = {
    "render_extraction": "Engine/Plugins/Runtime/ProceduralMeshComponent/Source/ProceduralMeshComponent/Private/KismetProceduralMeshLibrary.cpp:454-495",
    "zero_threshold": "Engine/Source/Editor/UnrealEd/Private/Fbx/FbxStaticMeshImport.cpp:365-373",
    "float32_degenerate_filter": "Engine/Source/Editor/UnrealEd/Private/Fbx/FbxStaticMeshImport.cpp:850-884",
    "uv_flip": "Engine/Source/Editor/UnrealEd/Private/Fbx/FbxStaticMeshImport.cpp:925-929",
    "complete_fallback_copies_input": "Engine/Source/Developer/NaniteBuilder/Private/NaniteBuilder.cpp:791-795",
    "fallback_reduction_conditions": "Engine/Source/Developer/NaniteBuilder/Private/NaniteBuilder.cpp:913-920",
    "nanite_encoding_after_fallback": "Engine/Source/Developer/NaniteBuilder/Private/NaniteBuilder.cpp:942-986",
}
F32 = struct.Struct("<f")
U32 = struct.Struct("<I")
MAX_SCALAR_ULPS = 2
CENTROID_CELL_CM = .01  # Search acceleration only, not an acceptance tolerance.


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def f32(x):
    y = F32.unpack(F32.pack(float(x)))[0]
    if not math.isfinite(y):
        raise ValueError("Nonfinite/overflowing float32 mesh value")
    return 0.0 if y == 0.0 else y


def _ordered_float(x):
    b = U32.unpack(F32.pack(f32(x)))[0]
    return 0x80000000 - (b & 0x7fffffff) if b & 0x80000000 else 0x80000000 + b


def ulp_distance(a, b):
    return abs(_ordered_float(a) - _ordered_float(b))


def _canonical(corners):
    corners = tuple(corners)
    return min(corners, corners[1:] + corners[:1], corners[2:] + corners[:2])


def _key(material, corners):
    return material, _canonical(corners)


def _centroid_cell(material, corners):
    return (material,) + tuple(math.floor(sum(p[i] for p in corners) / 3 / CENTROID_CELL_CM) for i in range(3))


def _zero_reason(points_cm):
    # Reproduce the FVector3f edge/cross operations at the importer boundary.
    a = tuple(f32(points_cm[1][i] - points_cm[2][i]) for i in range(3))
    b = tuple(f32(points_cm[0][i] - points_cm[2][i]) for i in range(3))
    cross = tuple(f32(f32(a[j] * b[k]) - f32(a[k] * b[j])) for j, k in [(1, 2), (2, 0), (0, 1)])
    if len(set(points_cm)) < 3:
        return "coincident_vertices_after_float32_centimetre_conversion"
    if cross == (0., 0., 0.):
        return "zero_float32_cross_product_at_importer"
    return None


def _index(text, size):
    i = int(text)
    if i == 0:
        raise ValueError("OBJ index zero is invalid")
    i = i - 1 if i > 0 else size + i
    if i < 0 or i >= size:
        raise ValueError("OBJ index outside available data")
    return i


def read_source_obj(path, reverse_winding=True):
    positions, uvs, normals, faces = [], [], [], []
    material = None
    with Path(path).open(encoding="utf-8-sig") as source:
        for line_number, line in enumerate(source, 1):
            parts = line.split()
            if not parts or parts[0].startswith("#"):
                continue
            tag, data = parts[0], parts[1:]
            if tag == "v":
                if len(data) != 3:
                    raise ValueError("Prepared OBJ must contain three-component vertices")
                positions.append(tuple(f32(float(x) * 100.) for x in data))
            elif tag == "vt":
                if len(data) < 2:
                    raise ValueError("Invalid OBJ UV")
                uvs.append((f32(data[0]), f32(1. - f32(data[1]))))
            elif tag == "vn":
                normals.append(tuple(f32(x) for x in data[:3]))
            elif tag == "usemtl":
                material = " ".join(data)
            elif tag == "f":
                if len(data) != 3 or not material:
                    raise ValueError("Prepared OBJ must contain triangulated, material-bound faces")
                corners, normal_corners, vertex_ids = [], [], []
                for token in data:
                    ids = token.split("/")
                    if len(ids) < 2 or not ids[1]:
                        raise ValueError("Every OBJ corner requires explicit UV0")
                    vi, ui = _index(ids[0], len(positions)), _index(ids[1], len(uvs))
                    ni = _index(ids[2], len(normals)) if len(ids) > 2 and ids[2] else None
                    corners.append(positions[vi] + uvs[ui])
                    normal_corners.append(normals[ni] if ni is not None else None)
                    vertex_ids.append(vi + 1)
                if reverse_winding:
                    corners.reverse(); normal_corners.reverse(); vertex_ids.reverse()
                corners, normal_corners = tuple(corners), tuple(normal_corners)
                zero_reason = _zero_reason(tuple(x[:3] for x in corners))
                # Missing-normal duplicates are not eligible for an exemption.
                full_key = (material, _canonical(tuple(c + n for c, n in zip(corners, normal_corners)))) if all(n is not None for n in normal_corners) else None
                faces.append({"material":material, "corners":corners, "normals":normal_corners,
                              "line":line_number, "vertex_indices_1based":vertex_ids,
                              "zero_reason":zero_reason, "key":_key(material,corners), "full_key":full_key})
    if not faces:
        raise ValueError("OBJ has no triangles")
    return faces


def _corner_match(source, native):
    maximum = 0
    for a, b in zip(source, native):
        d = ulp_distance(a, b)
        if d > MAX_SCALAR_ULPS:
            return None
        maximum = max(maximum, d)
    return maximum


def _face_match(source, native):
    best = None
    for shift in range(3):
        rotated = native[shift:] + native[:shift]
        maximum = 0
        for a, b in zip(source, rotated):
            d = _corner_match(a, b)
            if d is None:
                break
            maximum = max(maximum, d)
        else:
            best = maximum if best is None else min(best, maximum)
    return best


def compare_triangle_records(source_faces, native_faces):
    exact, cells = defaultdict(list), defaultdict(list)
    for i, face in enumerate(source_faces):
        exact[face["key"]].append(i)
        cells[_centroid_cell(face["material"],face["corners"])].append(i)
    used = bytearray(len(source_faces))
    matched_full_keys = set()
    unmatched_native, rounded_matches = [], 0
    maximum_ulp, native_count = 0, 0
    for native in native_faces:
        native_count += 1
        material, corners = native["material"], tuple(native["corners"])
        k = _key(material,corners)
        stack = exact.get(k)
        chosen = None
        while stack:
            i = stack.pop()
            if not used[i]:
                chosen = i; break
        distance = 0
        if chosen is None:
            cell = _centroid_cell(material,corners)
            for delta in product((-1,0,1), repeat=3):
                nearby = (material,) + tuple(cell[i+1]+delta[i] for i in range(3))
                for i in cells.get(nearby,()):
                    if used[i]:
                        continue
                    d = _face_match(source_faces[i]["corners"],corners)
                    if d is not None and (chosen is None or d < distance):
                        chosen, distance = i, d
                if chosen is not None and distance == 0:
                    break
            if chosen is not None:
                rounded_matches += 1
        if chosen is None:
            unmatched_native.append({"native_triangle":native_count-1,"material":material,
                                     "corners_cm_uv0":corners,"section":native.get("section")})
            continue
        used[chosen] = 1
        maximum_ulp = max(maximum_ulp,distance)
        full_key = source_faces[chosen]["full_key"]
        if full_key is not None:
            matched_full_keys.add(full_key)
    exemptions, missing = [], []
    for i, face in enumerate(source_faces):
        if used[i]:
            continue
        reason = face["zero_reason"]
        if reason is None and face["full_key"] is not None and face["full_key"] in matched_full_keys:
            reason = "duplicate_with_identical_position_uv_material_normal_and_winding"
        record = {"source_triangle":i,"obj_line":face["line"],"material":face["material"],
                  "vertex_indices_1based":face["vertex_indices_1based"],"corners_cm_uv0":face["corners"]}
        if reason:
            record["proof"]=reason
            exemptions.append(record)
        else:
            record["float32_zero_area"]=False
            missing.append(record)
    passed = not missing and not unmatched_native and native_count == len(source_faces)-len(exemptions)
    return {"status":"passed" if passed else "failed","source_triangles":len(source_faces),
            "native_render_triangles":native_count,"matched_triangles":sum(used),
            "proven_absent_degenerate_or_duplicate_triangles":len(exemptions),
            "unknown_missing_triangles":len(missing),"unexpected_native_triangles":len(unmatched_native),
            "scalar_rounding_match_triangles":rounded_matches,"max_matched_scalar_ulp":maximum_ulp,
            "allowed_scalar_ulp":MAX_SCALAR_ULPS,"per_face_exceptions":exemptions,
            "missing_source_faces":missing,"unmatched_native_faces":unmatched_native}


def extract_native_faces(mesh):
    import unreal
    text = unreal.MapImportLibrary.describe_map_mesh(mesh)
    if not text:
        raise RuntimeError("Native mesh diagnostics are unavailable")
    diagnostics = json.loads(text)
    sections = diagnostics.get("sections")
    if not sections or not diagnostics.get("full_precision_uvs"):
        raise RuntimeError("Render sections and full-precision UV0 are required")
    if int(diagnostics.get("uv_channels",0)) < 1:
        raise RuntimeError("Native render mesh has no UV0")
    for section_index, section in enumerate(sections):
        data = unreal.ProceduralMeshLibrary.get_section_from_static_mesh(mesh,0,section_index)
        if len(data) != 5:
            raise RuntimeError("Unexpected UE GetSectionFromStaticMesh result shape")
        vertices, indices, normals, uvs, tangents = data
        if len(vertices) != len(uvs) or len(indices) != int(section["triangles"])*3:
            raise RuntimeError("Native section buffers differ from render-data diagnostics")
        material = str(section["material_slot"])
        if material == "INVALID":
            raise RuntimeError("Native section has invalid material slot")
        payload = [tuple(f32(getattr(p,k)) for k in ("x","y","z"))+
                   tuple(f32(getattr(uv,k)) for k in ("x","y"))
                   for p,uv in zip(vertices,uvs)]
        for offset in range(0,len(indices),3):
            ids = [int(x) for x in indices[offset:offset+3]]
            if min(ids) < 0 or max(ids) >= len(payload):
                raise RuntimeError("Native render index outside section vertex buffer")
            yield {"material":material,"corners":tuple(payload[i] for i in ids),"section":section_index}


def verify_static_mesh_against_obj(mesh, source_path, *, expected_source_triangles=None,
                                  report_path=None, reverse_winding=True, raise_on_failure=True):
    """Call once after final build/asset compilation; never alters the asset."""
    source_path = Path(source_path).resolve()
    asset = mesh.get_path_name()
    if not asset.startswith(ASSET_PREFIX):
        raise ValueError("Geometry verification is scoped to SchoolGymFidelity")
    faces = read_source_obj(source_path,reverse_winding=reverse_winding)
    if expected_source_triangles is not None and len(faces) != int(expected_source_triangles):
        raise ValueError("Prepared manifest triangle count differs from source OBJ")
    result = compare_triangle_records(faces,extract_native_faces(mesh))
    nanite = mesh.get_editor_property("nanite_settings")
    nanite_enabled = bool(nanite.get_editor_property("enabled"))
    nanite_settings = {}
    for name in ("fallback_target", "fallback_percent_triangles", "fallback_relative_error",
                 "keep_percent_triangles", "trim_relative_error", "position_precision"):
        value = nanite.get_editor_property(name)
        nanite_settings[name] = value if isinstance(value, (bool, int, float, str)) else str(value)
    result.update(native_representation="nanite_fallback_lod0" if nanite_enabled else "conventional_lod0",
                  native_lod0_buffer_triangles=result["native_render_triangles"],
                  nanite_enabled=nanite_enabled, nanite_settings=nanite_settings,
                  nanite_cluster_geometry={"audited":False,"representation":"separate Nanite clusters"})
    render_count = int(mesh.get_num_triangles(0))
    if render_count != result["native_render_triangles"]:
        result["status"]="failed"
        result["render_triangle_count_mismatch"]={"mesh_get_num_triangles":render_count,
                                                "extracted":result["native_render_triangles"]}
    result.update(asset=asset,source_obj=str(source_path),source_sha256=sha(source_path),
                  checker_sha256=sha(__file__),scope="StaticMesh LODResources[0] positions, UV0, material slots and oriented triangles; Nanite fallback for Nanite-enabled assets, conventional LOD0 otherwise",
                  coordinate_contract={"source_units":"metres","native_units":"centimetres",
                                       "positions":"native=source*100; transport Y compensation is external",
                                       "reverse_source_winding":reverse_winding,
                                       "uv0":"float32(u), float32(1 - float32(v))"},
                  engine_source_evidence=ENGINE_SOURCE_EVIDENCE)
    if report_path is not None:
        report_path=Path(report_path);report_path.parent.mkdir(parents=True,exist_ok=True)
        report_path.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    if result["status"]!="passed" and raise_on_failure:
        raise RuntimeError("Strict source/LODResources[0] geometry mismatch for "+asset+": "+
                           str(result["unknown_missing_triangles"])+" missing, "+
                           str(result["unexpected_native_triangles"])+" unexpected; report="+str(report_path))
    return result
