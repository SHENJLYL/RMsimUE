"""Run the opt-in unified-API campus boundary fixture against an explicit UE project.

Build and import are separate prerequisites. This script never edits project sources,
builds a module, changes the original map, or terminates an existing UE process.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

SPAWN_ID = "gym_south_guard"
CHECK_NAMES = (
    "physical_settle",
    "actual_closed_perimeter_contact",
    "closed_glass_boundary_blocks_continued_drive",
    "physical_brake_at_boundary",
    "physical_reverse_releases_wall_contact",
    "final_stop_inside_original_field",
)


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fingerprints(paths):
    return {key: {"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)}
            for key, path in sorted(paths.items())}


def relative_file(parent, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute() or ".." in Path(name).parts:
        raise ValueError("Expected a package-relative file: " + repr(name))
    path = (parent / name).resolve()
    path.relative_to(parent.resolve())
    return path


def find_manifest(map_file):
    candidates = [map_file.parent / "arena-shell.json"]
    candidates += [p / "assets/maps/rmuc2027/arena-shell.json" for p in list(map_file.parents)[:8]]
    return next((p for p in candidates if p.is_file()), None)


def existing_ue_processes():
    if os.name == "nt":
        command = ("@(Get-CimInstance Win32_Process | Where-Object { $_.Name -in "
                   "@('UnrealEditor.exe','UnrealEditor-Cmd.exe') } | "
                   "Select-Object ProcessId,Name,CommandLine) | ConvertTo-Json -Compress")
        result = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                                capture_output=True, check=True, timeout=20)
        text = result.stdout.decode("utf-8-sig").strip()
        values = json.loads(text) if text else []
        return [values] if isinstance(values, dict) else values
    values = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                name = (entry / "comm").read_text().strip()
                if name.startswith("UnrealEditor"):
                    values.append({"ProcessId": int(entry.name), "Name": name})
            except (OSError, ProcessLookupError):
                pass
    return values


def stop_owned(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, timeout=20, check=True)
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=5)


def inputs_and_scene(args, output):
    platform = "Win64" if sys.platform == "win32" else "Linux"
    if sys.platform not in ("win32", "linux"):
        raise ValueError("Windows and Linux executables are supported")
    editor = args.engine_root / "Engine/Binaries" / platform / (
        "UnrealEditor.exe" if platform == "Win64" else "UnrealEditor")
    binaries = args.project.parent / "Binaries" / platform
    index = binaries / "UnrealEditor.modules"
    names = ["UnrealEditor-RMSim.dll"] if platform == "Win64" else ["libUnrealEditor-RMSim.so", "UnrealEditor-RMSim.so"]
    if index.is_file():
        name = load(index).get("Modules", {}).get("RMSim")
        if isinstance(name, str) and Path(name).name == name:
            names.insert(0, name)
    module = next((binaries / n for n in names if (binaries / n).is_file()), None)
    if not editor.is_file() or module is None:
        raise FileNotFoundError("Build the selected UE project before running this fixture")
    source = args.project.parent / "Source/RMSim"
    requirements = {
        "VehicleRig.h": ["GetRecentHitAgainst", "GetFoliageContactBounds", "GetMotionGeneration"],
        "ImportLabGameMode.h": ["FCampusGymBoundaryProbeState", "TickCampusGymBoundaryProbe"],
        "ContactProbe.cpp": ["CampusGymBoundaryProbe", "TickCampusGymBoundaryProbe"],
        "CampusGymBoundaryProbe.cpp": ["campus_gym_actual_closed_boundary_v1"],
    }
    for name, tokens in requirements.items():
        text = (source / name).read_text(encoding="utf-8-sig")
        if any(token not in text for token in tokens):
            raise ValueError("Unified boundary fixture API/hook missing in " + str(source / name))
    manifest = args.arena_manifest or find_manifest(args.map_file)
    if manifest is None:
        raise FileNotFoundError("Supply --arena-manifest for the campus gym")
    document, scene = load(manifest), load(args.map_file)
    if (document.get("schema") != "rmsim_campus_gym_v1" or scene.get("id") != "rmuc2027"
            or document.get("bounds_m") != scene.get("bounds_m")):
        raise ValueError("The rmuc2027 scene and campus manifest must have matching source bounds")
    perimeter = document.get("perimeter", {})
    if perimeter.get("interaction") is not False or perimeter.get("doors") != "fixed_closed_decorative":
        raise ValueError("This fixture requires the fixed closed decorative-door contract")
    layout = relative_file(manifest.parent, document.get("layout"))
    paths = {"project": args.project, "module": module, "original_map": args.map_file,
             "rig": args.mesh_rig_file, "manifest": manifest, "layout": layout,
             "wrapper": Path(__file__).resolve()}
    if index.is_file():
        paths["module_index"] = index
    for file in source.iterdir():
        if file.suffix in (".h", ".cpp", ".cs"):
            paths["source/" + file.name] = file
    for file in (args.project.parent / "Config").glob("*.ini"):
        paths["config/" + file.name] = file
    asset_root = args.project.parent / "Content/RMSim/Maps/rmuc2027/Arena/Campus"
    assets = sorted(p for p in asset_root.rglob("*") if p.suffix in (".uasset", ".uexp", ".ubulk"))
    if not assets or len(assets) > 128:
        raise ValueError("Expected 1..128 installed campus asset files in the selected project")
    for file in assets:
        paths["asset/" + file.relative_to(asset_root).as_posix()] = file
    scene_dir = output / "scene"
    scene_dir.mkdir()
    scene["spawns"] = [p for p in scene.get("spawns", []) if p.get("id") != SPAWN_ID]
    scene["spawns"].append({"id": SPAWN_ID, "team": "blue", "position_m": [-11, -6.6, 0], "yaw_rad": -math.pi / 2})
    staged_map = scene_dir / "map.json"
    save(staged_map, scene)
    shutil.copyfile(manifest, scene_dir / "arena-shell.json")
    staged_layout = relative_file(scene_dir, document["layout"])
    staged_layout.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(layout, staged_layout)
    for label, file in (("staged_map", staged_map), ("staged_manifest", scene_dir / "arena-shell.json"),
                        ("staged_layout", staged_layout)):
        paths[label] = file
    if document.get("source"):
        source_manifest = relative_file(manifest.parent, document["source"])
        if source_manifest.is_file():
            paths["asset_source_manifest"] = source_manifest
    return editor, staged_map, paths


def native_checks(control, boundary, evidence):
    checks = control.get("checks", [])
    events = control.get("actual_wall_hit_observations", [])
    wall = control.get("wall_component")
    def number(key, minimum):
        value = control.get(key)
        return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value >= minimum
    pngs = list(evidence.rglob("*.png"))
    valid_pngs = bool(pngs) and all(p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" for p in pngs)
    return {
        "correct_complete_native_probe": control.get("probe") == "campus_gym_actual_closed_boundary_v1"
            and control.get("complete") is True and control.get("status") == "passed",
        "six_named_physical_checks_passed": tuple(c.get("name") for c in checks) == CHECK_NAMES
            and all(c.get("passed") is True for c in checks),
        "actual_exact_south_wall_contact": control.get("expected_wall") == "arena_guard_south"
            and bool(wall) and bool(events) and any(e.get("phase") == 1 for e in events)
            and any(e.get("phase") == 2 for e in events)
            and all(e.get("exact_wall_component") == wall and e.get("impulse_n_s", 0) > 0 for e in events),
        "no_reset_or_pose_override": control.get("pose_overrides_used") is False
            and control.get("contact_recovery_count") == 0
            and control.get("initial_motion_generation") is not None
            and control.get("initial_motion_generation") == control.get("final_motion_generation"),
        "chassis_and_wheel_bounds_stayed_inside": number("minimum_vehicle_envelope_boundary_margin_cm", 0)
            and control.get("boundary_envelope_checked_every_tick") is True,
        "physical_approach_and_reverse": number("peak_approach_actual_speed_m_s", .5)
            and number("reverse_clearance_cm", 60),
        "native_capture_files_present": control.get("captures") == 3 and len(pngs) >= 3 and valid_pngs,
        "closed_perimeter_96_queries_passed": boundary.get("status") == "passed"
            and boundary.get("sweep_samples") == 96 and boundary.get("sweep_passed") == 96
            and len(boundary.get("checks", [])) == 96
            and all(c.get("passed") is True for c in boundary.get("checks", []))
            and boundary.get("continuous_collision_walls") == 4 and boundary.get("fixed_gate_leaves") == 4
            and boundary.get("gate_interaction_enabled") is False and boundary.get("cosmetic_no_collision") is True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("engine-root", "project", "map-file", "mesh-rig-file", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--arena-manifest", type=Path)
    parser.add_argument("--timeout", type=float, default=180.)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or not 30 <= args.timeout <= 600:
        parser.error("--timeout must be 30..600 wall seconds")
    for name in ("engine_root", "project", "map_file", "mesh_rig_file", "output", "arena_manifest"):
        if getattr(args, name) is not None:
            setattr(args, name, getattr(args, name).resolve())
    if args.output.exists():
        parser.error("Use a fresh output directory; previous evidence is preserved")
    args.output.mkdir(parents=True)
    started, process, paths = time.monotonic(), None, {}
    report = {"schema": "rmsim_campus_boundary_fixture_v1", "status": "failed",
              "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "scope": "Unified-API physical south-wall fixture; independent of clean-main native scene queries",
              "performance_benchmark": False, "human_visual_review": "separate", "checks": {}}
    try:
        busy = existing_ue_processes()
        report["existing_ue_processes"] = busy
        if busy:
            raise RuntimeError("An existing UE process is running; coordinate with its owner before starting this fixture")
        editor, scene, paths = inputs_and_scene(args, args.output)
        report["inputs_before"] = fingerprints(paths)
        save(args.output / "inputs-before.json", report["inputs_before"])
        evidence = args.output / "evidence"
        command = [str(editor), str(args.project), "/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode",
                   "-game", "-RenderOffScreen", "-unattended", "-NoSound", "-NoSplash", "-NoShaderWorker",
                   "-ContactProbe", "-CampusGymBoundaryProbe", "-MapFile=" + str(scene), "-SpawnId=" + SPAWN_ID,
                   "-MeshRigFile=" + str(args.mesh_rig_file), "-EvidenceDir=" + str(evidence),
                   "-GymProbeOutput=" + str(args.output / "boundary-report.json"),
                   "-abslog=" + str(args.output / "runtime.log")]
        report["command"] = command
        save(args.output / "command.json", command)
        with (args.output / "stdout.log").open("wb") as log:
            process = subprocess.Popen(command, cwd=args.project.parent, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=os.name != "nt")
            report["pid"] = process.pid
            process.wait(timeout=args.timeout)
        report["exit_code"] = process.returncode
        control = load(evidence / "control-result.json")
        boundary = load(args.output / "boundary-report.json")
        report["checks"] = native_checks(control, boundary, evidence)
        report["checks"]["process_exit_zero"] = process.returncode == 0
        runtime = (args.output / "runtime.log").read_text(encoding="utf-8", errors="replace")
        report["checks"]["native_success_marker"] = "CAMPUS_GYM_BOUNDARY_PASSED" in runtime
        report["checks"]["no_fatal_runtime_error"] = re.search(
            r"Fatal error:|Assertion failed:|Unhandled Exception|LowLevelFatalError", runtime, re.I) is None
        report["native_physical_checks"] = [{k: c.get(k) for k in ("name", "passed", "detail")} for c in control.get("checks", [])]
        report["metrics"] = {k: control.get(k) for k in (
            "approach_command_speed_m_s", "peak_approach_actual_speed_m_s", "held_drive_seconds",
            "held_drive_advance_cm", "held_stopped_tail_seconds", "reverse_clearance_cm",
            "minimum_vehicle_envelope_boundary_margin_cm", "initial_motion_generation", "final_motion_generation",
            "contact_recovery_count", "maximum_lateral_drift_cm", "maximum_body_rise_cm", "elapsed_sim_seconds")}
        report["boundary_margin_scope"] = "GetFoliageContactBounds: chassis and wheels AABB versus original map XY rectangle; excludes other rig parts"
        report["speed_scope"] = "Peak speed observed during approach; no preimpact speed claim"
        report["source_reports"] = {str(p.relative_to(args.output)): digest(p) for p in
                                    (evidence / "control-result.json", args.output / "boundary-report.json")}
    except (Exception, KeyboardInterrupt) as error:
        report["error"] = repr(error)
    finally:
        if process is not None and process.poll() is None:
            try:
                stop_owned(process)
                report["owned_process_stopped"] = True
            except Exception as error:
                report["cleanup_error"] = repr(error)
        report["exit_code"] = process.poll() if process else None
        if paths and "inputs_before" in report:
            try:
                report["inputs_after"] = fingerprints(paths)
                report["checks"]["inputs_unchanged"] = report["inputs_before"] == report["inputs_after"]
            except Exception as error:
                report["input_verification_error"] = repr(error)
        if (report["checks"] and all(report["checks"].values()) and "error" not in report
                and "cleanup_error" not in report and "input_verification_error" not in report):
            report["status"] = "passed"
        report["elapsed_wall_seconds"] = time.monotonic() - started
        save(args.output / "verification.json", report)
    print(json.dumps({"status": report["status"], "report": str(args.output / "verification.json"),
                      "error": report.get("error"), "failed_checks": [k for k, v in report["checks"].items() if not v]}))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
