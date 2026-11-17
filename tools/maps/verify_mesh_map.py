"""Independent checks of imported scale and recorded native wheel-support poses."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    package, evidence = args.package, args.evidence
    result_path = evidence / "geometry-verification.json"
    if result_path.exists():
        raise ValueError("use fresh evidence; existing verification must not be overwritten")
    imported = read(package / "ue-import-report.json")
    source = read(package / "mesh-source.json")
    manifest = read(package / "map.json")
    report = read(evidence / "control-result.json")
    assert report["status"] == "passed" and report["complete"]
    assert report["map_id"] == manifest["id"] == imported["map_id"]
    assert imported["assets_imported"] == len(manifest["meshes"]) * 2
    errors = []
    for mesh in imported["meshes"]:
        for edge in ("lo", "hi"):
            for axis in range(3):
                opposite = "hi" if edge == "lo" else "lo"
                expected = mesh["source_bounds_m"][opposite if axis == 1 else edge][axis] * (-100 if axis == 1 else 100)
                errors.append(abs(mesh["ue_bounds_cm"][edge][axis] - expected))
    assert max(errors) < 0.01, "import changed map scale or coordinates"
    for key, folder in (("visuals", "visual"), ("collisions", "collision")):
        for part in source[key]:
            assert hashlib.sha256((package / folder / part["file"]).read_bytes()).hexdigest() == part["sha256"]
    slopes, alignment = [], []
    for check in report["checks"]:
        assert check["passed"]
        for phase in ("before", "after"):
            state = check[phase]
            assert state["terrain_following"] and not state["blocking_overlap"]
            assert len(state["ground_contacts_cm"]) >= 3
            x, y, z, w = state["actor"]["rotation_xyzw"]
            up = (2 * (x*z + w*y), 2 * (y*z - w*x), 1 - 2 * (x*x+y*y))
            normal = state["ground_normal"]
            assert abs(sum(v*v for v in normal)-1) < 1e-6
            dot = sum(a*b for a, b in zip(up, normal))
            error = math.degrees(math.acos(min(1, max(-1, dot))))
            alignment.append(error)
            slopes.append(math.degrees(math.acos(min(1, max(-1, normal[2])))))
            assert error < 0.001, "chassis is not aligned with measured support plane"
    result = {"map_id": manifest["id"], "status": "passed", "imported_assets": len(imported["meshes"]),
              "max_bounds_error_cm": max(errors), "max_support_alignment_error_deg": max(alignment),
              "sampled_slope_range_deg": [min(slopes), max(slopes)],
              "scope": "source FBX identity, native import bounds and recorded support poses; not calibrated physics"}
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
