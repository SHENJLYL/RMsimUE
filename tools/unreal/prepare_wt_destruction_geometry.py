"""Enable runtime CPU reads on the exact source-derived destruction meshes.

Run through Run-WTNativeScript.ps1 with -ReportArgument DestructionGeometryAudit.
Only allow_cpu_access is edited; no FBX import, mesh build, axis conversion,
material edits, collision authoring, or World save is performed here.
"""
import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
import traceback

import unreal


HARD_MESHES = (
    "prop_wood_fence_h",
    "prop_wood_fence_h_plank",
)
SOFT_MESHES = (
    "prop_bush_flowers_a",
    "prop_bush_flowers_b",
    "prop_bush_flowers_c",
    "prop_tree_poplar_bush_medium_a",
    "prop_tree_poplar_bush_medium_b",
    "prop_tree_tilia_bush_a",
    "prop_tree_tilia_bush_b",
    "prop_flower_bed_b_flowers_a",
    "prop_flower_bed_c_flowers_a",
)
EXCLUDED_HARD = {
    # CPU-readable for geometry regression; whole thick-rail panels stay fixed.
    "prop_wood_fence_h": "anchored_thick_rail_panel",
    "prop_sicily_villa_railing_a": "masonry_balustrade",
    "prop_sicily_villa_railing_c": "masonry_balustrade",
    "prop_sicily_villa_railing_f": "masonry_balustrade",
    "prop_sicily_villa_railing_g": "masonry_balustrade",
    "prop_sicily_fence_cafe_a": "fixed_fence_assembly",
    "prop_sicily_fence_cafe_b": "fixed_fence_assembly",
    "prop_sicily_fence_cafe_c": "fixed_fence_assembly",
    "prop_sicily_fence_cafe_d": "fixed_fence_assembly",
    "prop_sicily_gardens_wood_planks_a": "large_wood_structure",
    "prop_sicily_gardens_wood_planks_b": "large_wood_structure",
    "prop_sicily_gardens_wood_planks_c": "large_wood_structure",
    "prop_wood_debris_a": "grouped_debris",
    "prop_wood_debris_b": "grouped_debris",
    "prop_wood_debris_e": "grouped_debris",
    "prop_wood_debris_f": "grouped_debris",
    "prop_wood_fence_m_pole": "anchored_structural_post",
    "prop_wood_fence_n_gate_double_a": "anchored_gate",
    "prop_wood_fence_o_gate_double_a": "anchored_gate",
    "prop_wood_gates_b": "anchored_gate",
    "prop_wood_gates_d": "anchored_gate",
}


def argument(name, default=None):
    match = re.search(r"-" + name + r'=(?:"([^"]+)"|(\S+))',
                      unreal.SystemLibrary.get_command_line())
    if match:
        return match.group(1) or match.group(2)
    if default is None:
        raise ValueError("pass -" + name + "=<path>")
    return default


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def local(package, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("input must be package-relative: " + str(relative))
    result = (package / path).resolve(strict=True)
    if not result.is_relative_to(package) or not result.is_file():
        raise ValueError("input escapes source package: " + str(relative))
    return result


def asset_file(asset):
    if not re.fullmatch(r"/Game/RMSim/Maps/wt_vlaanderen/(Meshes|Materials)/"
                        r"[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*", asset):
        raise ValueError("unexpected source-derived asset path: " + asset)
    package, object_name = asset.rsplit(".", 1)
    if package.rsplit("/", 1)[-1] != object_name:
        raise ValueError("asset package/object identity differs")
    return Path(unreal.Paths.project_content_dir()).resolve() / (
        package[len("/Game/"):] + ".uasset")


def snapshot(mesh):
    geometry = json.loads(unreal.MapImportLibrary.describe_map_mesh(mesh))
    bounds = mesh.get_bounding_box()
    materials = []
    for index, slot in enumerate(mesh.get_editor_property("static_materials")):
        material = mesh.get_material(index)
        materials.append({
            "slot": str(slot.get_editor_property("material_slot_name")),
            "asset": material.get_path_name() if material is not None else None,
        })
    return {
        "native_geometry": geometry,
        "bounds_cm": {
            "lo": [float(bounds.min.x), float(bounds.min.y), float(bounds.min.z)],
            "hi": [float(bounds.max.x), float(bounds.max.y), float(bounds.max.z)],
        },
        "materials": materials,
    }


def float32_ulp(value):
    """Spacing of one float32 step at the magnitude of this UE bound."""
    if not math.isfinite(value):
        raise ValueError("nonfinite native bound")
    bits = struct.unpack("<I", struct.pack("<f", abs(value)))[0]
    if bits >= 0x7f7fffff:
        raise ValueError("native bound is beyond finite float32 comparison range")
    rounded = struct.unpack("<f", struct.pack("<I", bits))[0]
    following = struct.unpack("<f", struct.pack("<I", bits + 1))[0]
    return following - rounded


def compare_native(before, after):
    if (before["native_geometry"] != after["native_geometry"]
            or before["materials"] != after["materials"]):
        raise ValueError("native topology/UV layout/material bindings changed")
    # UE stores FBoxSphereBounds as float32 origin and extent and reconstructs
    # endpoints with origin +/- extent. Near zero, cancellation makes endpoint
    # ULPs a poor error measure. Propagate round-to-nearest error instead:
    # each evaluation contributes half an ULP for origin, extent and its
    # endpoint result; subtracting two evaluations adds their error bounds.
    # No arbitrary distance tolerance is added to this arithmetic budget.
    axes = []
    for edge in ("lo", "hi"):
        for axis in range(3):
            endpoints = []
            terms = {}
            for label, native in (("before", before), ("after", after)):
                low = native["bounds_cm"]["lo"][axis]
                high = native["bounds_cm"]["hi"][axis]
                if not math.isfinite(low) or not math.isfinite(high) or high < low:
                    raise ValueError("invalid native bounds interval")
                origin = (low + high) * 0.5
                extent = (high - low) * 0.5
                endpoint = native["bounds_cm"][edge][axis]
                endpoints.append(endpoint)
                terms[label] = {
                    "reconstructed_origin_cm": origin,
                    "reconstructed_extent_cm": extent,
                    "origin_roundoff_cm": 0.5 * float32_ulp(origin),
                    "extent_roundoff_cm": 0.5 * float32_ulp(extent),
                    "endpoint_roundoff_cm": 0.5 * float32_ulp(endpoint),
                }
            error = abs(endpoints[0] - endpoints[1])
            budget = sum(value[key] for value in terms.values()
                         for key in ("origin_roundoff_cm", "extent_roundoff_cm",
                                     "endpoint_roundoff_cm"))
            if error > budget:
                raise ValueError("saved native bound exceeded propagated float32 roundoff")
            axes.append({"edge": edge, "axis": axis, "error_cm": error,
                         "roundoff_bound_cm": budget,
                         "fraction_of_roundoff_bound": error / budget,
                         "roundoff_terms": terms})
    return {"topology_uv_layout_material_bindings_exact": True,
            "bounds_error_model": "sum of half-ULP origin/extent/endpoint errors for both evaluations",
            "bounds_max_error_cm": max(item["error_cm"] for item in axes),
            "bounds_max_fraction_of_roundoff_bound": max(
                item["fraction_of_roundoff_bound"] for item in axes),
            "bounds_axes": axes}


def validate_native(identity, specification, native):
    if native["native_geometry"]["triangles"] != specification["triangles"]:
        raise ValueError("source/native triangle count differs: " + identity)
    slots = [item["slot"] for item in native["materials"]]
    if slots != specification["material_slots"]:
        raise ValueError("source/native material slot order differs: " + identity)
    for item in native["materials"]:
        expected = "/Game/RMSim/Maps/wt_vlaanderen/Materials/" + item["slot"]
        if item["asset"] != expected + "." + item["slot"]:
            raise ValueError("source material assignment differs: " + identity)
    # This is the existing importer contract: ROS metres -> UE centimetres,
    # with the Y sign mirrored. No transformation is applied to the mesh.
    for edge in ("lo", "hi"):
        for axis in range(3):
            source_edge = ("hi" if edge == "lo" else "lo") if axis == 1 else edge
            expected = specification["bounds_m"][source_edge][axis]
            expected *= -100.0 if axis == 1 else 100.0
            if abs(native["bounds_cm"][edge][axis] - expected) > 0.01:
                raise ValueError("source/native bounds differ: " + identity)


def prepare(source_file, output):
    source_file = Path(source_file).resolve(strict=True)
    package = source_file.parent
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema": "wt_destruction_cpu_geometry_v1",
        "status": "running",
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source_manifest": str(source_file),
        "source_manifest_sha256": digest(source_file),
        "script_sha256": digest(Path(__file__).resolve()),
        "requested_meshes": list(HARD_MESHES + SOFT_MESHES),
        "edited_property": "allow_cpu_access",
        "reimported": False,
        "world_saved": False,
        "geometry_rebuilt_by_script": False,
        "native_runtime_reload_verified": False,
        "assets": [],
    }
    save(output / "verification.json", report)
    protected = {}
    try:
        source = read(source_file)
        if (source.get("schema") != "rm_scene_map_source_v1"
                or source.get("id") != "wt_vlaanderen"
                or source.get("units") != "m"
                or source.get("coordinate_system") != "ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP"
                or not re.fullmatch("[0-9a-f]{64}", source.get("source_sha256", ""))):
            raise ValueError("expected source-bound Flanders map in ROS metres")
        report["original_scene_sha256"] = source["source_sha256"]
        instance_file = local(package, source["instances"])
        specifications = {item["id"]: item for item in source["meshes"]}
        parts = read(instance_file)["parts"]
        by_id = {item["id"]: item for item in parts}
        if len(specifications) != len(source["meshes"]) or len(by_id) != len(parts):
            raise ValueError("duplicate source mesh/instance identities")
        content = Path(unreal.Paths.project_content_dir()).resolve()
        world_file = content / "RMSim/Maps/wt_vlaanderen/World.umap"
        protected = {str(path): digest(path)
                     for path in (source_file, instance_file, world_file)}
        pending = []
        # Preflight every exact target before any asset is modified.
        for identity in HARD_MESHES + SOFT_MESHES:
            spec, part = specifications[identity], by_id[identity]
            expected = "/Game/RMSim/Maps/wt_vlaanderen/Meshes/" + identity + "." + identity
            if part["visual_asset"] != expected or part["instances_file"] != spec["instances_file"]:
                raise ValueError("source/instance asset binding differs: " + identity)
            fbx = local(package, spec["file"])
            instances = local(package, part["instances_file"])
            if digest(fbx) != spec["sha256"]:
                raise ValueError("source FBX hash mismatch: " + identity)
            raw = instances.read_bytes()
            if len(raw) < 8 or raw[:4] != b"RMI1":
                raise ValueError("invalid RMI stream: " + identity)
            count = struct.unpack_from("<I", raw, 4)[0]
            if count != spec["instance_count"] or len(raw) != 8 + count * 40:
                raise ValueError("source/RMI instance count mismatch: " + identity)
            mesh = unreal.load_asset(part["visual_asset"])
            if not isinstance(mesh, unreal.StaticMesh) or mesh.get_path_name() != expected:
                raise ValueError("native source mesh unavailable: " + identity)
            before = snapshot(mesh)
            validate_native(identity, spec, before)
            saved_file = asset_file(expected).resolve(strict=True)
            flag = bool(mesh.get_editor_property("allow_cpu_access"))
            record = {
                "id": identity, "asset": expected, "instances": count,
                "source_fbx": spec["file"], "source_fbx_sha256": spec["sha256"],
                "instances_sha256": digest(instances),
                "allow_cpu_access_before": flag,
                "package_sha256_before": digest(saved_file),
                "native_before": before,
                "backup_files": [],
            }
            report["assets"].append(record)
            for path in (fbx, instances):
                protected[str(path)] = digest(path)
            for binding in before["materials"]:
                path = asset_file(binding["asset"]).resolve(strict=True)
                protected[str(path)] = digest(path)
            pending.append((mesh, saved_file, record))
        report["protected_before"] = protected
        save(output / "verification.json", report)
        for mesh, saved_file, record in pending:
            if not record["allow_cpu_access_before"]:
                for suffix in (".uasset", ".uexp", ".ubulk"):
                    original = saved_file.with_suffix(suffix)
                    if not original.is_file():
                        continue
                    backup = output / "baseline" / original.relative_to(content)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, backup)
                    if digest(backup) != digest(original):
                        raise RuntimeError("asset baseline copy differs: " + str(original))
                    record["backup_files"].append({
                        "original": str(original), "backup": str(backup),
                        "sha256": digest(backup),
                    })
                mesh.set_editor_property("allow_cpu_access", True)
                if not unreal.EditorAssetLibrary.save_loaded_asset(mesh, only_if_is_dirty=False):
                    raise RuntimeError("could not save CPU-readable mesh: " + record["id"])
            record["allow_cpu_access_after"] = bool(mesh.get_editor_property("allow_cpu_access"))
            record["native_after"] = snapshot(mesh)
            record["package_sha256_after"] = digest(saved_file)
            if record["allow_cpu_access_after"] is not True:
                raise ValueError("CPU preparation flag did not persist: " + record["id"])
            record["native_preservation"] = compare_native(
                record["native_before"], record["native_after"])
            unreal.log("WT_DESTRUCTION_CPU_MESH " + json.dumps({
                "mesh": record["id"],
                "allow_cpu_access_before": record["allow_cpu_access_before"],
                "allow_cpu_access_after": record["allow_cpu_access_after"],
                "native_preservation": record["native_preservation"],
            }))
            save(output / "verification.json", report)
        negatives = []
        for identity, reason in EXCLUDED_HARD.items():
            spec = specifications[identity]
            negatives.append({
                "mesh": identity, "instances": spec["instance_count"],
                "expected_hard_breakable": False, "reason": reason,
                "source_fbx_sha256": spec["sha256"], "source_bounds_m": spec["bounds_m"],
            })
        negative_report = {
            "schema": "wt_destruction_policy_negative_cases_v1",
            "source_manifest_sha256": report["source_manifest_sha256"],
            "original_scene_sha256": source["source_sha256"],
            "instances": sum(item["instances"] for item in negatives),
            "cases": negatives,
            "native_policy_executed": False,
        }
        save(output / "negative-policy-cases.json", negative_report)
        report.update(status="passed", assets_checked=len(pending),
                      assets_changed=sum(not r["allow_cpu_access_before"] for _, _, r in pending),
                      excluded_hard_instances=negative_report["instances"],
                      negative_cases_sha256=digest(output / "negative-policy-cases.json"))
    except Exception as error:
        report.update(status="failed", error=str(error), traceback=traceback.format_exc())
        raise
    finally:
        after = {path: digest(Path(path)) for path in protected}
        report["protected_after"] = after
        report["source_fbx_instances_materials_world_unchanged"] = after == protected
        if after != protected:
            report.update(status="failed", error="protected source/material/world bytes changed")
        report["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(output / "verification.json", report)
    if report["status"] != "passed":
        raise RuntimeError(report["error"])
    unreal.log("WT_DESTRUCTION_CPU_GEOMETRY_PASSED " + str(output / "verification.json"))


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    prepare(argument("SceneSource"), argument(
        "DestructionGeometryAudit",
        str(root / "artifacts/maps/wt-destruction-fix-20261118/cpu-geometry")))
