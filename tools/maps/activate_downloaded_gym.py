"""Publish the verified downloaded gym layout and its outside-field perimeter."""
from pathlib import Path
import argparse
import hashlib
import json
import os


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("D:/simulator"))
    parser.add_argument("--import-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    output = args.output.resolve()
    venue = root / "assets/maps/rmuc2027/arena/downloaded-gym"
    prepared_path = venue / "prepared/prepared.json"
    prepared = json.loads(prepared_path.read_text(encoding="utf-8-sig"))
    imported = json.loads(args.import_report.read_text(encoding="utf-8-sig"))
    if imported["status"] != "passed" or imported["prepared_sha256"] != sha(prepared_path):
        raise RuntimeError("Native import must pass for this exact prepared model")
    if prepared["field_clearance"]["intersecting_triangle_count"] != 0:
        raise RuntimeError("Venue intersects the original competition field")
    if len(prepared["meshes"]) != len(imported["meshes"]):
        raise RuntimeError("Incomplete native model")
    source = venue / "source/indoor-basketball-court.glb"
    if sha(source) != prepared["source"]["sha256"]:
        raise RuntimeError("Downloaded source identity mismatch")
    output.mkdir(parents=True, exist_ok=False)

    original_path = root / "assets/maps/rmuc2027/arena-shell.json"
    original_bytes = original_path.read_bytes()
    manifest = json.loads(original_bytes.decode("utf-8-sig"))
    (output / "arena-shell.before.json").write_bytes(original_bytes)
    layout = {"schema": "rmsim_campus_gym_layout_v1", "units": "m",
              "coordinate_system": "ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP",
              "native_meshes": {}, "groups": [], "lights": [],
              "source": prepared["source"], "prepared_sha256": sha(prepared_path),
              "source_geometry_policy": "Whole downloaded gym, uniformly scaled; original RMUC field and vehicle unchanged."}
    for mesh, native in zip(prepared["meshes"], imported["meshes"]):
        name = mesh["asset_name"]
        if not native["asset"].endswith("/" + name + "." + name):
            raise RuntimeError("Native mesh order/name mismatch")
        low, high = mesh["bounds_min_m"], mesh["bounds_max_m"]
        center = [(low[i] + high[i]) * .5 for i in range(3)]
        center[1] *= -1  # OBJ is already UE XYZ; runtime layout positions are ROS.
        size = [high[i] - low[i] for i in range(3)]
        layout["native_meshes"][name] = native["asset"]
        layout["groups"].append({"id": name, "mesh": name, "material": "source",
                                  "cast_shadow": mesh.get("cast_shadow", True),
                                  "instances": [{"center": center, "size": size,
                                                 "rotation_deg": [0, 0, 0]}]})
    # Match the three authored luminaire rows; emit just below their housings.
    for y in (-12.0, 0.0, 12.0):
        z = 11.18 if y == 0 else 10.22
        for x in (-9.0, -3.0, 3.0, 9.0):
            layout["lights"].append({"center": [x, y, z], "rotation_deg": [0, 90, 0],
                                     "width_m": 3.0, "height_m": .65,
                                     "cast_shadows": True})
    manifest.update(layout="arena/downloaded-gym/layout.json", source="arena/downloaded-gym/SOURCE.json",
                    render_profile="RMSimGymLumen", navigation_package="navigation-console-outside",
                    asset_license="CC-BY-4.0; supplementary PBR CC0; official DJI/RoboMaster brand assets",
                    notes="Downloaded complete indoor sports gym by nguyennghiakk, CC BY 4.0. Native architectural geometry, official branding, fine PBR materials and Lumen lighting. Original competition CAD and vehicle geometry unchanged; closed perimeter is outside source bounds.")
    perimeter = manifest["perimeter"]
    perimeter.pop("inset_m", None)
    perimeter.update(outside_clearance_m=.001, physics_contract="outside_source_bounds_v1",
                     visual_alignment="source_bounds_outer_face")
    manifest["lighting"].update(mode="downloaded_gym_lumen", gi_method="lumen", reflection_method="lumen",
                               rect_light_lumens=45000, shadowed_rect_lights=12,
                               sun_intensity=30000, sky_intensity=12.0, exposure_ev100=8.0,
                               ambient_cubemap_intensity=0.0, background_brightness=4096.0)
    write_json(output / "layout.candidate.json", layout)
    write_json(output / "arena-shell.candidate.json", manifest)
    if args.apply:
        if original_path.read_bytes() != original_bytes:
            raise RuntimeError("Active scene changed while preparing; nothing overwritten")
        write_json(venue / "layout.json", layout)
        write_json(original_path, manifest)
    report = {"status": "applied_pending_native_visual_acceptance" if args.apply else "prepared",
              "prepared_sha256": sha(prepared_path), "source_sha256": sha(source),
              "arena_manifest_before_sha256": hashlib.sha256(original_bytes).hexdigest(),
              "arena_manifest_candidate_sha256": sha(output / "arena-shell.candidate.json"),
              "arena_manifest_current_sha256": sha(original_path), "native_meshes": len(layout["groups"]),
              "lights": len(layout["lights"]), "venue_floor_top_m": prepared["target_floor_top_z_m"],
              "competition_source_map_sha256": sha(root / "assets/maps/rmuc2027/map.json"),
              "activation_backup": str(output / "arena-shell.before.json")}
    write_json(output / "report.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
