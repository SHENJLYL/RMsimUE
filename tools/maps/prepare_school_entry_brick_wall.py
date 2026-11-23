"""Prepare the downloaded Grab3D CC0 brick wall without changing its surfaces.

Run with host Python, NumPy and Pillow. Source GLB/notice files stay read-only.
All original vertices, triangles, normals, UVs and decoded texture pixels are
retained. OBJ uses the existing school importer metre/Z-up convention.
The original GLB contains one material and no vertex colors or UV transforms.
"""
from __future__ import annotations

import argparse
import collections
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image, __version__ as PILLOW_VERSION
import prepare_school_entry_wall as helpers

ASSET = "RMExteriorEntryBrickWall"
MATERIAL = "EntryBrickWall"
PINNED_SHA256 = "91565e4a4f70009254fb48c98cb7584d603341aedf4123809c3446adaf06809b"


def accessor(document, binary, index):
    item = document["accessors"][index]
    if item.get("sparse") or item.get("normalized"):
        raise ValueError("Unexpected source accessor representation")
    view = document["bufferViews"][item["bufferView"]]
    components = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}[item["type"]]
    dtype = np.dtype({5123: "<u2", 5126: "<f4"}[item["componentType"]])
    if view.get("byteStride", dtype.itemsize * components) != dtype.itemsize * components:
        raise ValueError("Unexpected interleaved accessor")
    return np.frombuffer(
        binary, dtype=dtype, count=item["count"] * components,
        offset=view.get("byteOffset", 0) + item.get("byteOffset", 0),
    ).reshape(item["count"], components).copy()


def ray_coverage(vertices, faces, axis, ranges, counts):
    """Test first geometric surface normal from both directions of an axis."""
    corners = vertices[faces.astype(np.int64)]
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    axes = [i for i in range(3) if i != axis]
    a, b, c = [corners[:, i][:, axes] for i in range(3)]
    denominator = ((b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) +
                   (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1]))
    valid = np.abs(denominator) > 1.e-12
    denominator = np.where(valid, denominator, 1.)
    total = covered = negative = positive = 0
    distances_negative, distances_positive = [], []
    lower, upper = vertices[:, axis].min(), vertices[:, axis].max()
    for x in np.linspace(*ranges[0], counts[0]):
        for y in np.linspace(*ranges[1], counts[1]):
            aa = ((b[:, 1] - c[:, 1]) * (x - c[:, 0]) +
                  (c[:, 0] - b[:, 0]) * (y - c[:, 1])) / denominator
            bb = ((c[:, 1] - a[:, 1]) * (x - c[:, 0]) +
                  (a[:, 0] - c[:, 0]) * (y - c[:, 1])) / denominator
            cc = 1. - aa - bb
            hit = valid & (aa >= -1.e-9) & (bb >= -1.e-9) & (cc >= -1.e-9)
            total += 1
            if not hit.any():
                continue
            covered += 1
            location = aa * corners[:, 0, axis] + bb * corners[:, 1, axis] + cc * corners[:, 2, axis]
            matches = np.where(hit)[0]
            near = matches[np.argmin(location[matches])]
            far = matches[np.argmax(location[matches])]
            negative += int(cross[near, axis] < 0)
            positive += int(cross[far, axis] > 0)
            distances_negative.append(float(location[near] - lower))
            distances_positive.append(float(upper - location[far]))
    return {
        "ray_axis": "XYZ"[axis], "projection_axes": ["XYZ"[i] for i in axes],
        "ranges_m": ranges, "grid": counts, "samples": total,
        "any_surface": covered,
        "negative_camera_first_surface_outward": negative,
        "positive_camera_first_surface_outward": positive,
        "negative_first_hit_depth_from_bounds_m": {
            "min": min(distances_negative), "max": max(distances_negative),
            "median": float(np.median(distances_negative))},
        "positive_first_hit_depth_from_bounds_m": {
            "min": min(distances_positive), "max": max(distances_positive),
            "median": float(np.median(distances_positive))},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    source, out = args.source.resolve(strict=True), args.out.resolve()
    before = helpers.sha(source)
    if before != PINNED_SHA256:
        raise ValueError("Downloaded source identity changed")
    if out.exists():
        raise FileExistsError("Use a fresh output directory")
    document, binary = helpers.read_glb(source)
    if len(document["meshes"]) != 1 or len(document["materials"]) != 1:
        raise ValueError("Source mesh/material contract changed")
    if any(any(key in n for key in ("translation", "rotation", "scale", "matrix")) for n in document["nodes"]):
        raise ValueError("Unaccounted source node transform")
    primitive = document["meshes"][0]["primitives"][0]
    if len(document["meshes"][0]["primitives"]) != 1 or primitive.get("mode", 4) != 4:
        raise ValueError("Source primitive contract changed")
    if set(primitive["attributes"]) != {"POSITION", "NORMAL", "TEXCOORD_0"}:
        raise ValueError("Unaccounted source vertex color/attribute")
    arrays = {name: accessor(document, binary, index)
              for name, index in primitive["attributes"].items()}
    faces = accessor(document, binary, primitive["indices"]).reshape(-1, 3)
    if arrays["POSITION"].shape != (20860, 3) or faces.shape != (10412, 3):
        raise ValueError("Source topology differs from pinned model")
    if not all(np.isfinite(a).all() for a in arrays.values()):
        raise ValueError("Non-finite source attribute")
    source_material = document["materials"][0]
    pbr = source_material["pbrMetallicRoughness"]
    slots = {"BaseColor": pbr["baseColorTexture"], "NormalGL": source_material["normalTexture"],
             "MetallicRoughness": pbr["metallicRoughnessTexture"]}
    if any(item.get("texCoord", 0) != 0 or item.get("extensions") for item in slots.values()):
        raise ValueError("Unaccounted source UV set or texture transform")
    out.mkdir(parents=True)
    (out / "textures").mkdir()
    files, identities, paths = {}, {}, {}
    for name, texture_reference in slots.items():
        texture = document["textures"][texture_reference["index"]]
        image_index = texture["source"]
        entry = document["images"][image_index]
        view = document["bufferViews"][entry["bufferView"]]
        offset = view.get("byteOffset", 0)
        payload = binary[offset:offset + view["byteLength"]]
        relative = "textures/" + MATERIAL + "_" + name + ".png"
        path = out / relative
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            pixels = image.convert("RGBA").tobytes()
            mode, dimensions = image.mode, list(image.size)
            image.save(path, format="PNG", compress_level=6)
        with Image.open(path) as check:
            png_pixels = check.convert("RGBA").tobytes()
        if png_pixels != pixels:
            raise RuntimeError("PNG changed decoded source pixels")
        pixel_sha = helpers.bytes_sha(pixels)
        files[relative] = {
            "sha256": helpers.sha(path), "bytes": path.stat().st_size,
            "dimensions": dimensions, "decoded_rgba_sha256": pixel_sha}
        identities[name] = {
            "source_image_index": image_index, "source_mime_type": entry["mimeType"],
            "source_embedded_sha256": helpers.bytes_sha(payload), "source_embedded_bytes": len(payload),
            "source_mode": mode, "dimensions": dimensions,
            "source_decoded_rgba_sha256": pixel_sha,
            "png_decoded_rgba_sha256": helpers.bytes_sha(png_pixels),
            "decoded_pixels_identical": True, "resampled": False, "green_channel_flipped": False}
        paths[name] = relative
    # Proper rotation only: the source pivot is already centered at ground.
    positions = arrays["POSITION"].astype(np.float64)[:, [0, 2, 1]]
    positions[:, 1] *= -1.
    normals = arrays["NORMAL"].astype(np.float64)[:, [0, 2, 1]]
    normals[:, 1] *= -1.
    uv = arrays["TEXCOORD_0"].astype(np.float64).copy()
    uv[:, 1] = 1. - uv[:, 1]
    obj, mtl = out / (ASSET + ".obj"), out / (ASSET + ".mtl")
    with obj.open("w", encoding="utf-8", newline="\n") as writer:
        writer.write("# Grab3D CC0 brick wall: original source triangles and attributes\n")
        writer.write("mtllib " + mtl.name + "\no " + ASSET + "\n")
        for token, array in [("v", positions), ("vt", uv), ("vn", normals)]:
            for values in array:
                writer.write(token + " " + " ".join(format(float(x), ".17g") for x in values) + "\n")
        writer.write("usemtl " + MATERIAL + "\ns 1\n")
        for face in faces:
            writer.write("f " + " ".join(f"{int(i)+1}/{int(i)+1}/{int(i)+1}" for i in face) + "\n")
    mtl.write_text(
        "# Source PBR channels are declared in prepared.json\nnewmtl " + MATERIAL +
        "\nKd 1 1 1\nPr 1\nPm 1\nd 1\nmap_Kd " + paths["BaseColor"] +
        "\nmap_Bump " + paths["NormalGL"] + "\n", encoding="utf-8")
    parsed = {"v": [], "vt": [], "vn": [], "f": []}
    for line in obj.read_text(encoding="utf-8").splitlines():
        values = line.split()
        if values and values[0] in ("v", "vt", "vn"):
            parsed[values[0]].append([float(x) for x in values[1:]])
        elif values and values[0] == "f":
            corners = [[int(x)-1 for x in value.split("/")] for value in values[1:]]
            if any(len(set(c)) != 1 or len(c) != 3 for c in corners):
                raise ValueError("OBJ corner attribute identity changed")
            parsed["f"].append([c[0] for c in corners])
    for token, expected in [("v", positions), ("vt", uv), ("vn", normals), ("f", faces)]:
        if not np.array_equal(np.array(parsed[token]), expected):
            raise ValueError("OBJ roundtrip failed for " + token)
    surface = helpers.surface_integrity(positions, faces)
    components = surface["boundary_inspection"].pop("boundary_components")
    surface["boundary_inspection"]["boundary_component_count"] = len(components)
    surface["boundary_inspection"]["largest_boundary_components"] = components[:6]
    surface.pop("source_surface_classification")
    surface.pop("placement_note")
    surface["cardinal_coverage"] = {
        "front_and_back": ray_coverage(positions, faces, 1, [[-1.49, 1.49], [.01, 2.075]], [80, 40]),
        "end_sections": ray_coverage(positions, faces, 0, [[-.11, .11], [.01, 2.07]], [30, 60]),
        "coping_top": ray_coverage(positions, faces, 2, [[-1.49, 1.49], [-.15, .15]], [60, 20]),
    }
    surface["geometry_note"] = (
        "Both actual outward-facing masonry sides, coping and cut ends are retained. "
        "Separate source brick/mortar components have internal boundary edges; "
        "visual surface coverage and topological watertightness are reported separately."
    )
    material = {
        "semantic": "source", "source_material_name": source_material["name"],
        "base_color_factor": pbr.get("baseColorFactor", [1., 1., 1., 1.]),
        "roughness_factor": pbr.get("roughnessFactor", 1.),
        "metallic_factor": pbr.get("metallicFactor", 1.),
        "base_color_texture": paths["BaseColor"],
        "normal_texture": paths["NormalGL"], "normal_convention": "OpenGL",
        "normal_scale": source_material["normalTexture"].get("scale", 1.),
        "arm_texture": paths["MetallicRoughness"],
        "metallic_roughness_texture": paths["MetallicRoughness"],
        "arm_channels": {"ambient_occlusion": None, "roughness": "G", "metallic": "B"},
        "source_occlusion_texture_present": False,
        "ambient_occlusion_factor": 1.,
        "pbr_import_note": "R is unused in the source MR texture. Set native AO to 1; only G/B carry roughness/metallic.",
        "double_sided": source_material.get("doubleSided", False),
        "alpha_mode": source_material.get("alphaMode", "OPAQUE"),
        "source_pixels_modified": False,
    }
    source_hashes = {name: helpers.bytes_sha(a.tobytes()) for name, a in arrays.items()}
    source_hashes["INDICES"] = helpers.bytes_sha(faces.tobytes())
    bounds_min, bounds_max = positions.min(axis=0).tolist(), positions.max(axis=0).tolist()
    mesh = {
        "asset_name": ASSET, "file": obj.name, "bounds_min_m": bounds_min, "bounds_max_m": bounds_max,
        "vertices": len(positions), "triangles": len(faces), "material_slots": [MATERIAL],
        "sha256": helpers.sha(obj), "bytes": obj.stat().st_size, "cast_shadow": True, "source_instances": 1}
    verification = {
        "status": "passed", "source_sha256": before, "source_accessor_sha256": source_hashes,
        "source_vertices": len(positions), "source_triangles": len(faces),
        "source_vertex_attributes": list(primitive["attributes"]),
        "source_has_vertex_colors": False, "source_has_texture_transforms": False,
        "source_material_count": 1, "source_uv_sets": ["TEXCOORD_0"],
        "source_to_prepared": {"rotation": "glTF (x,y,z) -> (x,-z,y)", "uniform_scale": 1.,
                               "translation_m": [0., 0., 0.], "obj_uv": "(u,1-v)"},
        "geometry_changes": {"added_faces": 0, "removed_faces": 0, "decimation": False,
                             "welding": False, "normals_recomputed": False},
        "obj_roundtrip_exact": {"positions": True, "uv": True, "normals": True, "indices": True},
        "surface_integrity": surface, "mesh": mesh,
    }
    helpers.write_json(out / "geometry-verification.json", verification)
    helpers.write_json(out / "surface-integrity.json", surface)
    for path in [obj, mtl, out / "geometry-verification.json", out / "surface-integrity.json"]:
        files[path.name] = {"sha256": helpers.sha(path), "bytes": path.stat().st_size}
    if helpers.sha(source) != before:
        raise RuntimeError("Original source GLB changed")
    report = {
        "schema": "rmsim_school_gym_prepared_v1", "asset_preparation_schema": "rmsim_school_entry_brick_wall_v1",
        "status": "passed", "source_file": str(source), "source_file_sha256": before,
        "source_license": "CC0-1.0", "source_author": "Grab3D",
        "source_format": "Original downloaded GLB with exact attribute-preserving OBJ",
        "units": "metres, Z-up", "meshes": [mesh], "materials": {MATERIAL: material},
        "files": files, "texture_identity": identities, "source_pixels_modified": False,
        "geometry_verification": "geometry-verification.json", "surface_integrity": "surface-integrity.json",
        "source_vertices": len(positions), "source_triangles": len(faces),
        "script_sha256": helpers.sha(Path(__file__).resolve()),
        "helper_script_sha256": helpers.sha(Path(helpers.__file__).resolve()),
        "pillow_version": PILLOW_VERSION, "numpy_version": np.__version__,
        "prepared_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Source asset preparation; no active layout/C++/UE modifications.",
    }
    helpers.write_json(out / "prepared.json", report)
    print("ENTRY_BRICK_WALL_PREPARED", json.dumps(mesh), flush=True)
    print("COVERAGE", json.dumps(surface["cardinal_coverage"]), flush=True)
    print("PREPARED_SHA256", helpers.sha(out / "prepared.json"), flush=True)


if __name__ == "__main__":
    main()
