"""Add the actual closed perimeter to the preserved CAD navigation-map baseline.

Both maps retain the baseline's metric grid phase. Padding consists of whole
cells. No pose fitting, scan fitting, navigation parameter or tolerance is used.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zlib

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ros2_ws/src/rmsim_bridge"))
from rmsim_bridge.perimeter_geometry import canonical_sha256, load_closed_perimeter
from rmsim_bridge.navigation_map_identity import packaged_localization_map, package_relative_path
from export_localization_surface_map import projected_cells, read_pgm


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def document(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")


def write_pgm(path, pixels):
    h, w = pixels.shape
    Path(path).write_bytes(f"P5\n{w} {h}\n255\n".encode("ascii") + np.flipud(pixels).tobytes())


def item(path, root):
    return {"path": Path(path).resolve().relative_to(root).as_posix(), "sha256": sha(path)}


def padded_grid(info, geometry):
    r = float(info["resolution"])
    origin = [float(v) for v in info["origin"]]
    w, h = int(info["width"]), int(info["height"])
    if r <= 0 or not math.isfinite(r) or len(origin) != 3 or origin[2] != 0:
        raise ValueError("Boundary export requires a nonrotated metric baseline")
    lo = [min(wall["lo"][i] for wall in geometry["walls"]) for i in (0, 1)]
    hi = [max(wall["hi"][i] for wall in geometry["walls"]) for i in (0, 1)]
    # One extra cell beyond all physical walls preserves complete corner slabs.
    pad = max(1, *(math.ceil((origin[i]-lo[i])/r)+1 for i in (0, 1)),
              *(math.ceil((hi[i]-(origin[i]+(w, h)[i]*r))/r)+1 for i in (0, 1)))
    return dict(width=w+2*pad, height=h+2*pad, resolution=r,
                origin=[origin[0]-pad*r, origin[1]-pad*r, 0.], padding_cells=pad)


def generate(base, boundary_manifest, output, root=ROOT, baseline_raster_seed=None):
    root, base, boundary_manifest, output = [Path(p).resolve() for p in (root, base, boundary_manifest, output)]
    if output.exists():
        raise FileExistsError("Candidate output must be a new directory: " + str(output))
    spec = document(base / "SOURCE.json")
    if spec.get("runtime_boundary") is not None:
        raise ValueError("Use the original CAD baseline, not an already augmented package")
    scene = document(base / "scene.json")
    scene_geometry = {k: v for k, v in scene.items() if k not in ("spawns", "display_name", "limitations", "regions")}
    if canonical_sha256(scene_geometry) != spec["scene_geometry_sha256"]:
        raise ValueError("Baseline scene geometry identity mismatch")
    if sha(base / "map.pgm") != spec["raster_sha256"]:
        raise ValueError("Baseline planning PGM is missing or changed; restore its declared CAD export first")
    selected = packaged_localization_map(base, spec, allow_missing_derived=True)
    if not selected:
        raise ValueError("Original CAD baseline needs its declared localization package")
    if selected["missing_derived"]:
        subprocess.run([sys.executable, str(root / "tools/maps/export_localization_surface_map.py"),
                        "--restore-package", str(base / "SOURCE.json")], check=True)
        selected = packaged_localization_map(base, spec)
    local = Path(selected["yaml_path"]).parent
    local_meta = selected["metadata"]
    geometry, boundary_sha = load_closed_perimeter(boundary_manifest, scene["bounds_m"])
    if geometry["map_id"] != scene["id"]:
        raise ValueError("Boundary and scene map ids differ")
    platform_boundary = geometry["physics_contract"] == "outside_platform_bounds_v1"
    identity = {"schema": "rmsim_runtime_boundary_identity_v1", "sha256": boundary_sha, "geometry": geometry}
    info = spec["map_metadata"]
    grid = padded_grid(info, geometry)
    pad, w, h, r, origin = [grid[k] for k in ("padding_cells", "width", "height", "resolution", "origin")]
    original_nav, original_local = read_pgm(base / "map.pgm"), read_pgm(local / "map.pgm")
    if original_nav.shape != (info["height"], info["width"]) or original_local.shape != original_nav.shape:
        raise ValueError("Baseline planning/localization dimensions differ")
    for key in ("width", "height", "resolution", "origin"):
        if local_meta[key] != info[key]:
            raise ValueError("Baseline planning/localization spatial metadata differ: " + key)
    nav = np.pad(original_nav, pad, constant_values=0)
    localization = np.pad(original_local, pad, constant_values=205)
    footprint_cells, face_cells = set(), set()
    face_records = []
    groups = list(local_meta["groups"])
    for wall in geometry["walls"]:
        a, b = wall["lo"], wall["hi"]
        rectangle = [(a[0], a[1]), (b[0], a[1]), (b[0], b[1]), (a[0], b[1])]
        for row, col in projected_cells(rectangle, origin, r, w, h):
            footprint_cells.add((row, col)); nav[row, col] = 0
    for face in geometry["localization_faces"]:
        name = "runtime_perimeter_" + face["id"]
        if name in groups:
            raise ValueError("Baseline already contains a perimeter provenance group")
        group = len(groups); groups.append(name)
        for row, col in projected_cells([face["start_xy_m"], face["end_xy_m"]], origin, r, w, h):
            face_cells.add((row, col)); localization[row, col] = 0
            face_records.append((row, col, group, 2, face["bottom_m"], face["top_m"], 0., 0.))
    with np.load(local / "surface-provenance.npz", allow_pickle=False) as data:
        arrays = {key: data[key].copy() for key in data.files}
    columns = ("row", "col", "group_id", "contributing_triangles", "z_min_m", "z_max_m",
               "abs_normal_z_min", "abs_normal_z_max")
    if set(arrays) != set(columns):
        raise ValueError("Unsupported CAD surface provenance columns")
    arrays["row"] += pad; arrays["col"] += pad
    for index, key in enumerate(columns):
        arrays[key] = np.concatenate([arrays[key], np.array([a[index] for a in face_records], dtype=arrays[key].dtype)])
    order = np.lexsort((arrays["group_id"], arrays["col"], arrays["row"]))
    arrays = {key: value[order] for key, value in arrays.items()}
    output.mkdir(parents=True)
    target_local = output / "localization/vertical85"; target_local.mkdir(parents=True)
    write_pgm(output / "map.pgm", nav); write_pgm(target_local / "map.pgm", localization)
    baseline_yaml = yaml.safe_load((base / "map.yaml").read_text(encoding="utf-8-sig"))
    if baseline_yaml["origin"] != info["origin"] or baseline_yaml["resolution"] != r:
        raise ValueError("Baseline YAML/grid declaration differs")
    baseline_yaml["image"] = "map.pgm"; baseline_yaml["origin"] = origin
    yaml_text = yaml.safe_dump(baseline_yaml, sort_keys=False)
    for folder in (output, target_local):
        (folder / "map.yaml").write_text(yaml_text, encoding="utf-8", newline="\n")
    shutil.copyfile(base / "scene.json", output / "scene.json")
    np.savez_compressed(target_local / "surface-provenance.npz", **arrays)
    tool = root / "tools/maps/export_boundary_navigation_package.py"
    helper = root / "ros2_ws/src/rmsim_bridge/rmsim_bridge/perimeter_geometry.py"
    inputs = {"baseline_manifest": item(base / "SOURCE.json", root),
              "baseline_scene": item(base / "scene.json", root),
              "baseline_navigation_yaml": item(base / "map.yaml", root),
              "baseline_navigation_raster": item(base / "map.pgm", root),
              "baseline_localization_yaml": item(local / "map.yaml", root),
              "baseline_localization_raster": item(local / "map.pgm", root),
              "baseline_localization_metadata": item(local / "metadata.json", root),
              "baseline_surface_provenance": item(local / "surface-provenance.npz", root),
              "tool": item(tool, root), "geometry_helper": item(helper, root),
              "projection_tool": item(root / "tools/maps/export_localization_surface_map.py", root)}
    effective_scene = canonical_sha256({"source_scene_geometry_sha256": spec["scene_geometry_sha256"],
                                        "runtime_boundary_sha256": boundary_sha})
    nav_meta = copy.deepcopy(info)
    nav_meta.update({key: grid[key] for key in ("width", "height", "resolution", "origin")})
    nav_meta.update(runtime_boundary=identity, effective_scene_geometry_sha256=effective_scene,
                    status="generated_not_native_validated", free_cells=int((nav == 254).sum()),
                    boundary_padding_cells=pad, baseline_navigation_raster_sha256=spec["raster_sha256"],
                    boundary_projection="Conservative closed-cell XY intersection with the four physical collision slabs; no robot inflation")
    metadata = copy.deepcopy(local_meta)
    metadata.update({key: grid[key] for key in ("width", "height", "resolution", "origin")})
    metadata.update(status="generated_not_native_validated", runtime_boundary=identity,
                    effective_scene_geometry_sha256=effective_scene,
                    map_yaml_sha256=sha(target_local / "map.yaml"), raster_sha256=sha(target_local / "map.pgm"),
                    surface_provenance_sha256=sha(target_local / "surface-provenance.npz"),
                    reference_navigation_raster_sha256=sha(output / "map.pgm"),
                    reference_navigation_yaml_sha256=sha(output / "map.yaml"),
                    groups=groups, occupied_cells=int((localization == 0).sum()),
                    free_cells=int((localization == 254).sum()), unknown_cells=int((localization == 205).sum()),
                    cell_group_records=len(arrays["row"]), boundary_padding_cells=pad,
                    boundary_exporter_sha256=sha(tool), boundary_geometry_helper_sha256=sha(helper),
                    baseline_localization_metadata_sha256=sha(local / "metadata.json"),
                    runtime_boundary_added_face_records=len(face_records), runtime_boundary_face_cells=len(face_cells),
                    original_navigation_preserved=False, original_cad_geometry_preserved=True,
                    algorithm=("Original fixed vertical85 CAD projections retained at identical world coordinates; add actual inward-facing surfaces of the four closed collision slabs at the declared wave-platform boundary; whole-cell grid padding only." if platform_boundary else "Original fixed vertical85 CAD projections retained at identical world coordinates; add actual inward-facing surfaces of the four closed exterior collision slabs; whole-cell grid padding only."))
    # This names the immutable baseline manifest actually used above; it is not
    # the generated SOURCE.json (whose hash would form a circular dependency).
    metadata["reference_navigation_manifest_sha256"] = sha(base / "SOURCE.json")
    metadata["reference_navigation_manifest_role"] = "immutable_baseline_before_boundary_augmentation"
    metadata["selected_triangles"] = local_meta["selected_triangles"] + 8
    metadata["selected_triangles_by_group"] = {**local_meta["selected_triangles_by_group"],
        **{"runtime_perimeter_"+face["id"]: 2 for face in geometry["localization_faces"]}}
    metadata["baseline_occupied_over_prior_free_cells"] = metadata.pop("occupied_over_prior_free_cells", None)
    metadata["original_cad_exporter_sha256"] = metadata.pop("exporter_sha256")
    write_json(target_local / "metadata.json", metadata)
    declaration = copy.deepcopy(spec["localization_map"])
    declaration["status"] = "generated_not_native_validated"
    declaration["baseline_native_evidence"] = declaration.pop("native_evidence", {})
    declaration["baseline_native_evidence_scope"] = ("Historical CAD map only; it does not validate this platform-perimeter package." if platform_boundary else "Historical CAD map only; it does not validate this outside-perimeter package.")
    declaration["files"] = {role: {"path": "localization/vertical85/"+name, "sha256": sha(target_local / name)}
                             for role, name in (("yaml", "map.yaml"), ("raster", "map.pgm"),
                                                ("metadata", "metadata.json"), ("surface_provenance", "surface-provenance.npz"))}
    baseline_bytes = (base / "map.pgm").read_bytes()
    if baseline_raster_seed is None:
        baseline_raster_seed = {"encoding": "zlib-base64", "uncompressed_bytes": len(baseline_bytes),
                                "sha256": sha(base / "map.pgm"),
                                "data": base64.b64encode(zlib.compress(baseline_bytes, 6)).decode("ascii")}
    if decode_baseline_raster_seed(baseline_raster_seed, spec["raster_sha256"]) != baseline_bytes:
        raise ValueError("Immutable baseline raster seed differs from the actual planning PGM")
    declaration["export_recipe"] = {
        "schema": "rmsim_boundary_navigation_export_recipe_v1", "inputs": inputs,
        "baseline_navigation_raster_seed": baseline_raster_seed,
        "boundary_manifest_path": boundary_manifest.relative_to(root).as_posix(),
        "runtime_boundary_sha256": boundary_sha,
        "generated_files_policy": "Rebuild only missing planning PGM, localization PGM and NPZ; existing hash mismatches fail; tracked metadata and YAML remain unchanged.",
    }
    result = copy.deepcopy(spec)
    result.update(raster_sha256=sha(output / "map.pgm"), map_metadata=nav_meta,
                  runtime_boundary=identity, effective_scene_geometry_sha256=effective_scene,
                  boundary_manifest_path=boundary_manifest.relative_to(root).as_posix(),
                  localization_map=declaration, status="generated_not_native_validated",
                  scope=("Preserved CAD navigation baseline with closed walls aligned to the declared wave-platform perimeter; native boundary and AMCL/Nav2 validation recorded separately." if platform_boundary else "Preserved CAD navigation baseline with outside-only closed perimeter; new native boundary and AMCL/Nav2 validation required."))
    result["baseline_verified_route"] = result.pop("verified_route", [])
    result["baseline_provenance"] = result.pop("provenance", None)
    write_json(output / "SOURCE.json", result)
    old = (slice(pad, pad+original_nav.shape[0]), slice(pad, pad+original_nav.shape[1]))
    unchanged_nav = (nav[old] == original_nav)
    unchanged_local = (localization[old] == original_local)
    report = {
        "status": "generated_not_native_validated", "runtime_boundary_sha256": boundary_sha,
        "grid": grid, "navigation_raster_sha256": result["raster_sha256"],
        "localization_raster_sha256": metadata["raster_sha256"],
        "baseline_planning_cells_changed": int((~unchanged_nav).sum()),
        "baseline_localization_cells_changed": int((~unchanged_local).sum()),
        "wall_footprint_cells": len(footprint_cells), "inner_face_cells": len(face_cells),
        "cad_grid_world_positions_preserved": True,
        "source_inputs_unchanged": all(sha(root / value["path"]) == value["sha256"] for value in inputs.values()),
        "navigation_parameters_modified": False, "evaluation_poses_used": False,
    }
    if not report["source_inputs_unchanged"]:
        raise RuntimeError("Export input changed during generation")
    write_json(output / "generation-report.json", report)
    return report


def decode_baseline_raster_seed(seed, expected_sha):
    """Decode the small lossless original-raster source; never synthesize a map."""
    if not isinstance(seed, dict) or seed.get("encoding") != "zlib-base64" or seed.get("sha256") != expected_sha:
        raise ValueError("Invalid immutable baseline planning-raster seed")
    size = seed.get("uncompressed_bytes")
    if type(size) is not int or not 0 < size <= 16*1024*1024:
        raise ValueError("Baseline raster seed has invalid decoded size")
    compressed = base64.b64decode(seed["data"], validate=True)
    decoder = zlib.decompressobj()
    raw = decoder.decompress(compressed, size+1)
    if (len(raw) != size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
            or hashlib.sha256(raw).hexdigest() != expected_sha):
        raise ValueError("Immutable baseline planning-raster seed hash/size mismatch")
    return raw


def restore(manifest_path, root=ROOT):
    root, manifest_path = Path(root).resolve(), Path(manifest_path).resolve()
    spec = document(manifest_path)
    selected = packaged_localization_map(manifest_path.parent, spec, allow_missing_derived=True)
    recipe = selected["export_recipe"] if selected else {}
    if recipe.get("schema") != "rmsim_boundary_navigation_export_recipe_v1":
        raise ValueError("Expected an outside-perimeter package recipe")
    inputs = recipe["inputs"]
    # The preserved baseline also keeps its large localization binaries outside
    # Git. Rebuild those through their original immutable recipe before verifying
    # every recorded input for the boundary augmentation.
    baseline_item = inputs["baseline_manifest"]
    baseline_manifest = package_relative_path(root, baseline_item["path"], "boundary_recipe.baseline_manifest")
    if not baseline_manifest.is_file() or sha(baseline_manifest) != baseline_item["sha256"]:
        raise ValueError("Original CAD baseline manifest is missing or changed")
    baseline_spec = document(baseline_manifest)
    raster_item = inputs["baseline_navigation_raster"]
    baseline_raster = package_relative_path(root, raster_item["path"], "boundary_recipe.baseline_navigation_raster")
    baseline_seed = recipe.get("baseline_navigation_raster_seed")
    original_bytes = decode_baseline_raster_seed(baseline_seed, raster_item["sha256"])
    if not baseline_raster.is_file():
        with baseline_raster.open("xb") as stream:
            stream.write(original_bytes)
    elif sha(baseline_raster) != raster_item["sha256"]:
        raise ValueError("Existing original planning raster differs; preserving its bytes")
    baseline_selected = packaged_localization_map(baseline_manifest.parent, baseline_spec, allow_missing_derived=True)
    if baseline_selected and baseline_selected["missing_derived"]:
        subprocess.run([sys.executable, str(root / "tools/maps/export_localization_surface_map.py"),
                        "--restore-package", str(baseline_manifest)], check=True)
    paths = {}
    for name, value in inputs.items():
        path = package_relative_path(root, value["path"], "boundary_recipe."+name)
        if not path.is_file() or sha(path) != value["sha256"]:
            raise ValueError("Boundary export input missing or changed: "+str(path))
        paths[name] = path
    if paths["tool"] != Path(__file__).resolve():
        raise ValueError("Boundary recipe must use this exporter")
    boundary = package_relative_path(root, recipe["boundary_manifest_path"], "boundary_manifest")
    if load_closed_perimeter(boundary)[1] != recipe["runtime_boundary_sha256"]:
        raise ValueError("Perimeter changed; generate and validate a new package")
    files = {"map.pgm": spec["raster_sha256"]}
    for role in ("raster", "surface_provenance"):
        value = spec["localization_map"]["files"][role]; files[value["path"]] = value["sha256"]
    missing = []
    for name, expected in files.items():
        path = package_relative_path(manifest_path.parent, name, "generated_boundary_file")
        if not path.is_file():
            missing.append(name)
        elif sha(path) != expected:
            raise ValueError("Existing generated boundary file differs: "+name)
    if not missing:
        return {"status": "already_verified", "restored": []}
    with tempfile.TemporaryDirectory(prefix="rmsim-boundary-export-") as temporary:
        generated = Path(temporary) / "candidate"
        generate(paths["baseline_manifest"].parent, boundary, generated, root, baseline_seed)
        # Immutable declarations must rebuild exactly, as well as binary bytes.
        for name in ("SOURCE.json", "map.yaml", "scene.json", "localization/vertical85/map.yaml",
                     "localization/vertical85/metadata.json"):
            if (generated / name).read_bytes() != (manifest_path.parent / name).read_bytes():
                raise ValueError("Rebuilt boundary declaration differs: "+name)
        for name, expected in files.items():
            if sha(generated / name) != expected:
                raise ValueError("Rebuilt boundary bytes differ: "+name)
        for name in missing:
            with (manifest_path.parent / name).open("xb") as stream:
                stream.write((generated / name).read_bytes())
    packaged_localization_map(manifest_path.parent, spec)
    return {"status": "restored_and_verified", "restored": missing, "metadata_unchanged": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-package", type=Path)
    parser.add_argument("--boundary-manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--restore-package", type=Path)
    args = parser.parse_args(argv)
    if args.restore_package:
        result = restore(args.restore_package, args.repo_root)
    else:
        if not all((args.base_package, args.boundary_manifest, args.output)):
            parser.error("--base-package, --boundary-manifest and --output are required")
        result = generate(args.base_package, args.boundary_manifest, args.output, args.repo_root)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
