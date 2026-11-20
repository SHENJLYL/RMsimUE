"""Prepare the pinned CC0 entry wall without changing source mesh/UV/PBR data.

Run with host Python (Pillow required). Blender's bundled official glTF Draco
decoder is used in a separate background CPU process with automatic scripts
disabled. It decodes attributes only: no scene import, modifiers or remeshing.
The OBJ uses the existing school importer convention (metres, X/Y/Z-up).
A fresh output directory is mandatory; source GLB and notices remain read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
from datetime import datetime, timezone

ASSET = "RMExteriorEntryGraniteWall"
MATERIAL = "EntryGraniteWall"
PINNED_SHA256 = "25a6d20c7301fa94616b67e823f614326f6e5039f28ff012e21f5f6a0c77d6e9"


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bytes_sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def read_glb(path):
    content = Path(path).read_bytes()
    magic, version, size = struct.unpack_from("<4sII", content)
    if (magic, version, size) != (b"glTF", 2, len(content)):
        raise ValueError("Invalid GLB header")
    chunks, offset = [], 12
    while offset < len(content):
        length, kind = struct.unpack_from("<I4s", content, offset)
        offset += 8
        chunks.append((kind, content[offset:offset + length]))
        offset += length
    if offset != len(content) or [kind for kind, _ in chunks] != [b"JSON", b"BIN\0"]:
        raise ValueError("Expected exactly JSON and BIN GLB chunks")
    return json.loads(chunks[0][1]), chunks[1][1]


def source_contract(document):
    if len(document["meshes"]) != 1 or len(document["materials"]) != 1:
        raise ValueError("Pinned wall must have one mesh and one material")
    if len(document["nodes"]) != 1 or set(document["nodes"][0]) - {"name", "mesh"}:
        raise ValueError("Unaccounted source node transform")
    primitives = document["meshes"][0]["primitives"]
    if len(primitives) != 1 or primitives[0].get("mode", 4) != 4:
        raise ValueError("Expected one triangle primitive")
    primitive = primitives[0]
    if set(primitive["attributes"]) != {"POSITION", "NORMAL", "TEXCOORD_0"}:
        raise ValueError("Unaccounted vertex attribute")
    if "KHR_draco_mesh_compression" not in primitive.get("extensions", {}):
        raise ValueError("Pinned source must use its original Draco buffer")
    count = document["accessors"][primitive["indices"]]["count"]
    if count != 9116 * 3:
        raise ValueError("Pinned source triangle count changed")
    material = document["materials"][0]
    pbr = material["pbrMetallicRoughness"]
    references = [material["normalTexture"], material["occlusionTexture"],
                  pbr["baseColorTexture"], pbr["metallicRoughnessTexture"]]
    if any(r.get("texCoord", 0) != 0 or r.get("extensions") for r in references):
        raise ValueError("Unexpected source UV transform or nonzero UV set")
    if material["occlusionTexture"]["index"] != pbr["metallicRoughnessTexture"]["index"]:
        raise ValueError("AO and metallic/roughness must share the source ARM map")
    return primitive, material


def export_textures(document, binary, out):
    from PIL import Image
    import PIL
    primitive, source_material = source_contract(document)
    pbr = source_material["pbrMetallicRoughness"]
    roles = {
        "BaseColor": pbr["baseColorTexture"]["index"],
        "NormalGL": source_material["normalTexture"]["index"],
        "ARM": pbr["metallicRoughnessTexture"]["index"],
    }
    files, identities, relative_paths = {}, {}, {}
    (out / "textures").mkdir()
    for role, index in roles.items():
        texture = document["textures"][index]
        image_index = texture.get("source", texture.get("extensions", {}).get(
            "EXT_texture_webp", {}).get("source"))
        image = document["images"][image_index]
        view = document["bufferViews"][image["bufferView"]]
        if view["buffer"] != 0 or image["mimeType"] != "image/webp":
            raise ValueError("Unexpected embedded source image")
        start = view.get("byteOffset", 0)
        embedded = binary[start:start + view["byteLength"]]
        if len(embedded) != view["byteLength"]:
            raise ValueError("Embedded image extent outside source binary")
        relative = "textures/" + MATERIAL + "_" + role + ".png"
        target = out / relative
        with Image.open(io.BytesIO(embedded)) as original:
            original.load()
            original_mode = original.mode
            # RGB/RGBA byte values are preserved; no gamma/profile conversion.
            decoded = original.convert("RGBA")
            source_pixels = decoded.tobytes()
            source_pixel_sha = bytes_sha(source_pixels)
            output = original.copy() if original.mode in ("RGB", "RGBA") else decoded
            output.save(target, format="PNG", compress_level=6)
            dimensions = list(original.size)
        with Image.open(target) as reopened:
            png_pixels = reopened.convert("RGBA").tobytes()
        if png_pixels != source_pixels:
            raise RuntimeError("Lossless decoded pixel identity failed: " + role)
        identity = {
            "source_image_index": image_index,
            "source_mime_type": image["mimeType"],
            "source_buffer_view": image["bufferView"],
            "source_binary_byte_offset": start,
            "source_bytes": len(embedded),
            "source_embedded_sha256": bytes_sha(embedded),
            "source_dimensions": dimensions,
            "source_mode": original_mode,
            "pixel_identity_format": "RGBA8, row-major top-to-bottom, no color transform",
            "source_decoded_rgba_sha256": source_pixel_sha,
            "png_decoded_rgba_sha256": bytes_sha(png_pixels),
            "decoded_pixels_identical": True,
            "resampled": False,
            "green_channel_flipped": False,
        }
        identities[role] = identity
        files[relative] = {
            "sha256": sha(target), "bytes": target.stat().st_size,
            "dimensions": dimensions, "decoded_rgba_sha256": source_pixel_sha,
            "source_embedded_sha256": identity["source_embedded_sha256"],
        }
        relative_paths[role] = relative
        print("WALL_TEXTURE_PRESERVED", role, dimensions, source_pixel_sha, flush=True)
    material = {
        "semantic": "source",
        "source_material_name": source_material.get("name"),
        "base_color_factor": pbr.get("baseColorFactor", [1., 1., 1., 1.]),
        "metallic_factor": pbr.get("metallicFactor", 1.),
        "roughness_factor": pbr.get("roughnessFactor", 1.),
        "base_color_texture": relative_paths["BaseColor"],
        "normal_texture": relative_paths["NormalGL"],
        "normal_convention": "OpenGL",
        "normal_scale": source_material["normalTexture"].get("scale", 1.),
        "arm_texture": relative_paths["ARM"],
        "arm_channels": {"ambient_occlusion": "R", "roughness": "G", "metallic": "B"},
        "occlusion_strength": source_material["occlusionTexture"].get("strength", 1.),
        "double_sided": source_material.get("doubleSided", False),
        "alpha_mode": source_material.get("alphaMode", "OPAQUE"),
        "emissive_factor": source_material.get("emissiveFactor", [0., 0., 0.]),
        "source_pixels_modified": False,
        "color_space": "sRGB base color; linear normal and ARM",
    }
    return files, identities, material, PIL.__version__


def surface_integrity(vertices, triangles):
    """Read-only orientation, open-edge and entrance-view coverage inspection."""
    import collections
    import numpy as np
    vertices = np.asarray(vertices, dtype=np.float64)
    triangles = np.asarray(triangles, dtype=np.int64)
    corners = vertices[triangles]
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    lengths = np.linalg.norm(cross, axis=1)
    normals = np.divide(cross, lengths[:, None], out=np.zeros_like(cross),
                        where=lengths[:, None] != 0)
    areas = lengths / 2.
    dominant = np.argmax(np.abs(normals), axis=1)
    distribution = {}
    for axis, label in enumerate("XYZ"):
        for sign in (-1, 1):
            mask = (dominant == axis) & (normals[:, axis] * sign > 0)
            distribution[("+" if sign > 0 else "-") + label] = {
                "triangles": int(mask.sum()), "area_m2": float(areas[mask].sum())}
    # Position identity welds attribute/UV seams for topology inspection only.
    # No source or prepared vertex is modified or removed.
    identities, mapping = {}, []
    for point in vertices:
        key = tuple(float(x) for x in point)
        identities.setdefault(key, len(identities))
        mapping.append(identities[key])
    edges = collections.Counter()
    for face in triangles:
        face_ids = [mapping[i] for i in face]
        for a, b in zip(face_ids, face_ids[1:] + face_ids[:1]):
            if a != b:
                edges[tuple(sorted((a, b)))] += 1
    boundary = [edge for edge, count in edges.items() if count == 1]
    graph = collections.defaultdict(set)
    for a, b in boundary:
        graph[a].add(b)
        graph[b].add(a)
    points = np.array(list(identities))
    seen, components = set(), []
    for start in graph:
        if start in seen:
            continue
        pending, component = [start], set()
        while pending:
            current = pending.pop()
            if current in component:
                continue
            component.add(current)
            pending.extend(graph[current] - component)
        seen.update(component)
        selected = points[list(component)]
        components.append({
            "vertices": len(component), "bounds_min_m": selected.min(axis=0).tolist(),
            "bounds_max_m": selected.max(axis=0).tolist(),
            "closed_boundary_loop": all(len(graph[i]) == 2 for i in component),
        })
    xs = np.linspace(vertices[:, 0].min() + .12, vertices[:, 0].max() - .12, 80)
    zs = np.linspace(.15, vertices[:, 2].max() - .10, 30)
    a, b, c = [corners[:, i][:, [0, 2]] for i in range(3)]
    denominator = ((b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) +
                   (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1]))
    valid = np.abs(denominator) > 1.e-12
    safe_denominator = np.where(valid, denominator, 1.)
    samples = covered = negative_outward = positive_outward = 0
    for x in xs:
        for z in zs:
            aa = ((b[:, 1] - c[:, 1]) * (x - c[:, 0]) +
                  (c[:, 0] - b[:, 0]) * (z - c[:, 1])) / safe_denominator
            bb = ((c[:, 1] - a[:, 1]) * (x - c[:, 0]) +
                  (a[:, 0] - c[:, 0]) * (z - c[:, 1])) / safe_denominator
            cc = 1. - aa - bb
            hit = valid & (aa >= -1.e-9) & (bb >= -1.e-9) & (cc >= -1.e-9)
            samples += 1
            if not hit.any():
                continue
            covered += 1
            ys = aa * corners[:, 0, 1] + bb * corners[:, 1, 1] + cc * corners[:, 2, 1]
            hits = np.where(hit)[0]
            negative_outward += int(normals[hits[np.argmin(ys[hits])], 1] < 0)
            positive_outward += int(normals[hits[np.argmax(ys[hits])], 1] > 0)
    return {
        "coordinate_system": "prepared Z-up, metres",
        "triangle_normal_area_by_dominant_axis": distribution,
        "boundary_inspection": {
            "position_identity_only_weld": True, "geometry_modified": False,
            "unique_positions": len(identities), "boundary_edges": len(boundary),
            "nonmanifold_edges": sum(count > 2 for count in edges.values()),
            "boundary_components": sorted(components, key=lambda x: -x["vertices"]),
            "watertight": not boundary and all(count == 2 for count in edges.values()),
        },
        "interior_wall_orthographic_rays_along_Y": {
            "samples": samples, "grid": [80, 30],
            "x_range_m": [float(xs[0]), float(xs[-1])],
            "z_range_m": [float(zs[0]), float(zs[-1])],
            "any_surface_with_source_double_sided_material": covered,
            "negative_Y_camera_first_surface_outward": negative_outward,
            "positive_Y_camera_first_surface_outward": positive_outward,
        },
        "source_surface_classification": "single-sided open photogrammetry shell",
        "placement_note": (
            "The photographed wall front points toward -Y. A complete +Y-facing "
            "back surface is absent. Two-sided shading shows the reverse of the "
            "same shell; it does not close the top/end perimeter. Use separate "
            "back-to-back source instances or a complete two-sided asset when "
            "both entrance sides are visible. No source geometry was changed."
        ),
    }


def audit_existing(out, source):
    manifest_path = out / "prepared.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["source_file_sha256"] != sha(source):
        raise ValueError("Audit source identity differs from prepared source")
    entry = manifest["meshes"][0]
    obj = out / entry["file"]
    if sha(obj) != entry["sha256"]:
        raise ValueError("Audit OBJ differs from prepared mesh")
    vertices, triangles = [], []
    with obj.open(encoding="utf-8") as stream:
        for line in stream:
            fields = line.split()
            if fields and fields[0] == "v":
                vertices.append([float(value) for value in fields[1:]])
            elif fields and fields[0] == "f":
                triangles.append([int(value.split("/")[0]) - 1 for value in fields[1:]])
    result = surface_integrity(vertices, triangles)
    result.update(
        source_sha256=sha(source), prepared_obj_sha256=sha(obj),
        audit_script_sha256=sha(Path(__file__).resolve()))
    path = out / "surface-integrity.json"
    write_json(path, result)
    manifest["surface_integrity"] = path.name
    manifest["source_surface_type"] = result["source_surface_classification"]
    manifest["files"][path.name] = {"sha256": sha(path), "bytes": path.stat().st_size}
    # Atomic manifest replacement permits an importer to read only complete JSON.
    temporary = out / "prepared.json.audit-tmp"
    write_json(temporary, manifest)
    temporary.replace(manifest_path)
    print("WALL_SURFACE_AUDIT", json.dumps(result), flush=True)


def blender_stage(source, out):
    import bpy
    import numpy as np
    from io_scene_gltf2.io.imp.gltf2_io_gltf import glTFImporter
    from io_scene_gltf2.io.imp.gltf2_io_binary import BinaryData
    from io_scene_gltf2.blender.imp.draco_compression_extension import decode_primitive

    document, binary = read_glb(source)
    primitive_json, source_material = source_contract(document)
    gltf = glTFImporter(str(source), {"import_user_extensions": [], "loglevel": 30})
    gltf.read()
    gltf.checks()
    primitive = gltf.data.meshes[0].primitives[0]
    decode_primitive(gltf, primitive)
    attributes = {name: BinaryData.decode_accessor(gltf, accessor).copy()
                  for name, accessor in primitive.attributes.items()}
    positions = attributes["POSITION"]
    normals = attributes["NORMAL"]
    uv = attributes["TEXCOORD_0"]
    indices = BinaryData.decode_accessor(gltf, primitive.indices).copy().reshape(-1, 3)
    if (positions.shape, normals.shape, uv.shape, indices.shape) != (
            (6344, 3), (6344, 3), (6344, 2), (9116, 3)):
        raise ValueError("Decoded source topology differs from source accessors")
    if not all(np.isfinite(a).all() for a in [positions, normals, uv]):
        raise ValueError("Non-finite source attributes")
    if indices.max() >= len(positions):
        raise ValueError("Source triangle references an invalid vertex")
    # Proper rotation (determinant +1): glTF (x,y,z) -> Z-up (x,-z,y).
    canonical = positions.astype(np.float64)[:, [0, 2, 1]]
    canonical[:, 1] *= -1.
    rotated_min, rotated_max = canonical.min(axis=0), canonical.max(axis=0)
    offset = np.array([-(rotated_min[0] + rotated_max[0]) / 2.,
                       -(rotated_min[1] + rotated_max[1]) / 2.,
                       -rotated_min[2]])
    canonical += offset
    canonical_normals = normals.astype(np.float64)[:, [0, 2, 1]]
    canonical_normals[:, 1] *= -1.
    canonical_uv = uv.astype(np.float64).copy()
    canonical_uv[:, 1] = 1. - canonical_uv[:, 1]
    obj = out / (ASSET + ".obj")
    mtl = out / (ASSET + ".mtl")
    with obj.open("w", encoding="utf-8", newline="\n") as writer:
        writer.write("# CC0 Nik Kottmann Granite Wall; all source triangles preserved\n")
        writer.write("mtllib " + mtl.name + "\no " + ASSET + "\n")
        for array, token in [(canonical, "v"), (canonical_uv, "vt"), (canonical_normals, "vn")]:
            for values in array:
                writer.write(token + " " + " ".join(format(float(x), ".17g") for x in values) + "\n")
        writer.write("usemtl " + MATERIAL + "\ns 1\n")
        for face in indices:
            writer.write("f " + " ".join(f"{int(i)+1}/{int(i)+1}/{int(i)+1}" for i in face) + "\n")
    mtl.write_text(
        "# PBR ARM channel semantics are recorded in prepared.json\n"
        "newmtl " + MATERIAL + "\nKd 1 1 1\nKs 0.04 0.04 0.04\nPr 1\nPm 0\nd 1\n"
        "map_Kd textures/" + MATERIAL + "_BaseColor.png\n"
        "map_Bump textures/" + MATERIAL + "_NormalGL.png\n", encoding="utf-8")
    # Independently parse the transport text: every source corner remains mapped
    # to its source position/normal/UV index and original triangle ordering.
    parsed = {"v": [], "vt": [], "vn": [], "f": []}
    with obj.open(encoding="utf-8") as reader:
        for line in reader:
            parts = line.split()
            if not parts:
                continue
            if parts[0] in ("v", "vt", "vn"):
                parsed[parts[0]].append([float(v) for v in parts[1:]])
            elif parts[0] == "f":
                corners = [[int(v) - 1 for v in c.split("/")] for c in parts[1:]]
                if any(len(c) != 3 or len(set(c)) != 1 for c in corners):
                    raise ValueError("OBJ corner attribute mapping changed")
                parsed["f"].append([c[0] for c in corners])
    for token, array in [("v", canonical), ("vt", canonical_uv), ("vn", canonical_normals), ("f", indices)]:
        if not np.array_equal(np.asarray(parsed[token]), array):
            raise ValueError("OBJ roundtrip lost source attribute precision: " + token)
    source_bounds = [positions.min(axis=0).tolist(), positions.max(axis=0).tolist()]
    target_bounds = [canonical.min(axis=0).tolist(), canonical.max(axis=0).tolist()]
    corners = canonical[indices.astype(np.int64)]
    crosses = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    twice_area = np.linalg.norm(crosses, axis=1)
    surface_normal = crosses.sum(axis=0)
    length = float(np.linalg.norm(surface_normal))
    if length:
        surface_normal /= length
    height = target_bounds[1][2] - target_bounds[0][2]
    arrays_sha = {name: bytes_sha(np.ascontiguousarray(a).astype(
                    a.dtype.newbyteorder("<"), copy=False).tobytes())
                  for name, a in attributes.items()}
    arrays_sha["INDICES"] = bytes_sha(np.ascontiguousarray(indices).astype(
        indices.dtype.newbyteorder("<"), copy=False).tobytes())
    record = {
        "asset_name": ASSET, "file": obj.name,
        "bounds_min_m": target_bounds[0], "bounds_max_m": target_bounds[1],
        "triangles": int(indices.shape[0]), "vertices": int(positions.shape[0]),
        "material_slots": [MATERIAL], "sha256": sha(obj), "bytes": obj.stat().st_size,
        "cast_shadow": True, "source_instances": 1,
    }
    report = {
        "status": "passed", "source_vertices": len(positions),
        "source_triangles": len(indices), "exported_vertices": len(parsed["v"]),
        "exported_triangles": len(parsed["f"]),
        "source_accessor_sha256": arrays_sha,
        "source_accessor_dtypes": {k: str(a.dtype) for k, a in attributes.items()},
        "source_index_dtype": str(indices.dtype),
        "source_bounds_y_up_m": source_bounds,
        "declared_accessor_bounds_y_up_m": [
            document["accessors"][primitive_json["attributes"]["POSITION"]]["min"],
            document["accessors"][primitive_json["attributes"]["POSITION"]]["max"]],
        "source_to_prepared": {
            "rotation": "glTF (x,y,z) -> canonical (x,-z,y)",
            "rotation_determinant": 1,
            "translation_m": offset.tolist(), "uniform_scale": 1.,
            "pivot": "XY bounding-box center, lowest source vertex at Z=0",
            "uv_mapping": "OBJ (u,1-v); source texture pixels unchanged",
        },
        "geometry_changes": {"decimation": False, "added_faces": 0, "removed_faces": 0,
                             "welding": False, "topology_reordered": False,
                             "source_normals_recomputed": False},
        "strict_zero_area_source_triangles": int(np.count_nonzero(twice_area == 0)),
        "surface_area_m2": float(twice_area.sum() / 2.),
        "signed_area_average_normal_xyz": surface_normal.tolist(),
        "obj_roundtrip_exact": {"positions": True, "uv": True, "normals": True, "indices": True},
        "source_uv_sha256": arrays_sha["TEXCOORD_0"],
        "obj_uv_float64_sha256": bytes_sha(canonical_uv.astype("<f8").tobytes()),
        "recommended_uniform_scale": 1.,
        "source_height_m": height,
        "uniform_scale_for_1_3m_height": 1.3 / height,
        "blender_version": bpy.app.version_string,
        "blender_binary": bpy.app.binary_path,
        "blender_draco_decoder": "io_scene_gltf2.blender.imp.draco_compression_extension",
        "mesh": record,
    }
    write_json(out / "geometry-verification.json", report)
    print("WALL_GEOMETRY_PASSED", json.dumps(record), flush=True)


def main():
    stage = "--blender-stage" in sys.argv
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--blender-stage", action="store_true")
    parser.add_argument("--audit-existing", action="store_true")
    arguments = sys.argv[sys.argv.index("--") + 1:] if stage else sys.argv[1:]
    args = parser.parse_args(arguments)
    source, out = args.source.resolve(strict=True), args.out.resolve()
    before = sha(source)
    if before != PINNED_SHA256:
        raise ValueError("Pinned source GLB identity mismatch")
    if args.audit_existing:
        audit_existing(out, source)
        return
    if stage:
        blender_stage(source, out)
        if sha(source) != before:
            raise RuntimeError("Source GLB changed during geometry preparation")
        return
    if out.exists():
        raise FileExistsError("Choose a fresh output directory: " + str(out))
    if not args.blender or not args.blender.resolve().is_file():
        raise ValueError("Provide an existing --blender executable")
    if source.is_relative_to(out):
        raise ValueError("Source must remain outside generated output")
    document, binary = read_glb(source)
    source_contract(document)
    out.mkdir(parents=True)
    files, identities, material, pillow_version = export_textures(document, binary, out)
    command = [str(args.blender.resolve()), "--background", "--factory-startup",
               "--disable-autoexec", "--python", str(Path(__file__).resolve()), "--",
               "--blender-stage", "--source", str(source), "--out", str(out)]
    started = datetime.now(timezone.utc).isoformat()
    with (out / "blender-conversion.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, text=True)
    write_json(out / "conversion-execution.json", {
        "command": command, "started_utc": started,
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "exit_code": result.returncode, "source_sha256_before": before,
        "source_sha256_after": sha(source), "script_sha256": sha(Path(__file__).resolve()),
        "scope": "CPU asset conversion; no UE process or active layout is accessed",
    })
    if result.returncode:
        raise RuntimeError("Blender conversion failed; see blender-conversion.log")
    if sha(source) != before:
        raise RuntimeError("Read-only source changed")
    geometry = json.loads((out / "geometry-verification.json").read_text(encoding="utf-8"))
    for path in [out / (ASSET + ".obj"), out / (ASSET + ".mtl"), out / "geometry-verification.json"]:
        files[path.name] = {"sha256": sha(path), "bytes": path.stat().st_size}
    report = {
        "schema": "rmsim_school_gym_prepared_v1",
        "asset_preparation_schema": "rmsim_school_entry_wall_fidelity_v1",
        "status": "passed", "source_file": str(source), "source_file_sha256": before,
        "source_format": "Original pinned CC0 GLB; official Blender Draco decode; source-preserving OBJ",
        "source_license": "CC0-1.0", "source_author": "Nik Kottmann",
        "units": "metres, Z-up",
        "meshes": [geometry["mesh"]], "materials": {MATERIAL: material}, "files": files,
        "texture_identity": identities, "pillow_version": pillow_version,
        "geometry_verification": "geometry-verification.json",
        "source_geometry_sha256": geometry["source_accessor_sha256"],
        "source_vertices": geometry["source_vertices"], "source_triangles": geometry["source_triangles"],
        "source_transform": geometry["source_to_prepared"],
        "source_pixels_modified": False,
        "script_sha256": sha(Path(__file__).resolve()),
        "prepared_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Source geometry/UV/PBR preparation only; UE import and in-scene appearance are separately verified.",
    }
    write_json(out / "prepared.json", report)
    audit_existing(out, source)
    print("SCHOOL_ENTRY_WALL_PREPARED", str(out), geometry["mesh"], flush=True)


if __name__ == "__main__":
    main()
