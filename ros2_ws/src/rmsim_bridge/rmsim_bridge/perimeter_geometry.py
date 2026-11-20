"""Metric geometry for closed walls outside an explicitly selected field boundary.

This module consumes declarations only. It reads neither simulated nor estimated poses.
The venue's appearance is deliberately absent from the physical identity.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


def canonical_sha256(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode("utf-8")).hexdigest()


def closed_perimeter_geometry(manifest: dict, source_bounds=None) -> dict:
    if manifest.get("units") != "m" or manifest.get("coordinate_system") != "ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP":
        raise ValueError("Perimeter requires metres and ROS right-handed coordinates")
    bounds = manifest.get("bounds_m")
    if not isinstance(bounds, dict) or set(bounds) != {"lo", "hi"}:
        raise ValueError("Perimeter requires source bounds")
    if source_bounds is not None and bounds != source_bounds:
        raise ValueError("Perimeter bounds differ from the source competition scene")
    lo, hi = bounds["lo"], bounds["hi"]
    for triple in (lo, hi):
        if not isinstance(triple, list) or len(triple) != 3 or any(
                type(v) not in (int, float) or not math.isfinite(v) for v in triple):
            raise ValueError("Perimeter bounds must contain finite metric triples")
    if any(a >= b for a, b in zip(lo, hi)):
        raise ValueError("Perimeter bounds have no positive volume")
    p = manifest.get("perimeter", {})
    contract = p.get("physics_contract")
    if (contract not in ("outside_source_bounds_v1", "outside_platform_bounds_v1") or
            p.get("mode") != "continuous_closed" or
            p.get("doors") != "fixed_closed_decorative" or p.get("interaction") is not False):
        raise ValueError("Perimeter requires a permanently closed declared-boundary contract")
    target = bounds
    if contract == "outside_platform_bounds_v1":
        target = p.get("target_bounds_m")
        if not isinstance(target, dict) or set(target) != {"lo", "hi"}:
            raise ValueError("Platform perimeter requires explicit target bounds")
        for triple in (target["lo"], target["hi"]):
            if not isinstance(triple, list) or len(triple) != 3 or any(
                    type(v) not in (int, float) or not math.isfinite(v) for v in triple):
                raise ValueError("Platform target bounds must contain finite metric triples")
        if any(a >= b for a, b in zip(target["lo"], target["hi"])):
            raise ValueError("Platform target bounds have no positive volume")
        if any(abs(target[side][axis]-bounds[side][axis]) > 1.e-7
               for side in ("lo", "hi") for axis in (0, 2)):
            raise ValueError("Platform target must preserve the source X and Z bounds")
        if target["lo"][1] < lo[1] or target["hi"][1] > hi[1]:
            raise ValueError("Platform target Y bounds must remain inside the source field")
    values = {}
    for key, minimum, maximum in (("outside_clearance_m", 0., .02),
                                  ("collision_thickness_m", .1, .75),
                                  ("collision_bottom_m", -3., -.5),
                                  ("collision_top_m", hi[2]+.6, 10.)):
        value = p.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError("Invalid perimeter dimension: " + key)
        values[key] = float(value)
    clearance = values["outside_clearance_m"]
    depth = values["collision_thickness_m"]
    bottom, top = values["collision_bottom_m"], values["collision_top_m"]
    xmin, xmax = target["lo"][0]-clearance, target["hi"][0]+clearance
    ymin, ymax = target["lo"][1]-clearance, target["hi"][1]+clearance
    # The strips overlap in each exterior corner square. Their nearest faces
    # remain outside the selected target rectangle regardless of strip thickness.
    boxes = [
        ("west", [xmin-depth, ymin-depth, bottom], [xmin, ymax+depth, top]),
        ("east", [xmax, ymin-depth, bottom], [xmax+depth, ymax+depth, top]),
        ("south", [xmin-depth, ymin-depth, bottom], [xmax+depth, ymin, top]),
        ("north", [xmin-depth, ymax, bottom], [xmax+depth, ymax+depth, top]),
    ]
    faces = [
        ("west", [xmin, ymin], [xmin, ymax]),
        ("east", [xmax, ymin], [xmax, ymax]),
        ("south", [xmin, ymin], [xmax, ymin]),
        ("north", [xmin, ymax], [xmax, ymax]),
    ]
    return {
        "schema": "rmsim_closed_perimeter_geometry_v1",
        "map_id": manifest.get("map_id"),
        "units": "m", "coordinate_system": manifest["coordinate_system"],
        "source_bounds_m": {"lo": list(lo), "hi": list(hi)},
        "physics_contract": contract, **values,
        **({"target_bounds_m": {"lo": list(target["lo"]), "hi": list(target["hi"])}}
           if contract == "outside_platform_bounds_v1" else {}),
        "collision_channels": {"Camera": "Ignore", "Visibility": "Block", "physics": "Block"},
        "walls": [{"id": name, "lo": a, "hi": b} for name, a, b in boxes],
        "localization_faces": [{"id": name, "start_xy_m": a, "end_xy_m": b,
                                "bottom_m": bottom, "top_m": top} for name, a, b in faces],
        "localization_visibility": "inward faces visible from the continuously enclosed field",
    }


def load_closed_perimeter(path, source_bounds=None) -> tuple[dict, str]:
    manifest = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    geometry = closed_perimeter_geometry(manifest, source_bounds)
    return geometry, canonical_sha256(geometry)


def verify_boundary_metadata(metadata: dict) -> dict | None:
    """Validate a runtime map's boundary against the live package declaration."""
    declaration = metadata.get("runtime_boundary")
    if declaration is None:
        return None
    if not isinstance(declaration, dict) or declaration.get("schema") != "rmsim_runtime_boundary_identity_v1":
        raise ValueError("Invalid runtime-boundary map identity")
    geometry = declaration.get("geometry")
    if not isinstance(geometry, dict) or canonical_sha256(geometry) != declaration.get("sha256"):
        raise ValueError("Runtime-boundary geometry identity mismatch")
    path = metadata.get("runtime_boundary_manifest_path")
    if not isinstance(path, str) or not Path(path).is_file():
        raise ValueError("Runtime map lacks its active perimeter manifest")
    actual, actual_sha = load_closed_perimeter(path, geometry["source_bounds_m"])
    if actual_sha != declaration["sha256"] or actual != geometry:
        raise ValueError("Physical perimeter changed; regenerate the boundary navigation package")
    return declaration
