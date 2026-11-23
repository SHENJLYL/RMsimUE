"""Run the native third-person aim-occlusion regression on Windows or Linux.

Build the selected RMSim project first. This tool never builds, imports assets,
edits sources, connects to ROS, or stops an existing simulator process.
NullRHI validates native control/physics/traces; rendered runs also decode the
three capture sets. OS input delivery, image approval and performance are separate.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
import zlib

CHECKS = (
    "occlusion_supported_manual_spawn",
    "occlusion_precondition_view_170_degrees",
    "behind_muzzle_camera_hit_preserves_forward_aim",
    "occluded_mouse_right_down_crosses_yaw_seam",
    "occluded_mouse_left_up_crosses_yaw_seam",
    "occluded_c_shift_keeps_gimbal_direction",
    "occluded_c_release_returns_scope_without_flip",
    "occluded_scope_exit_mouse_resumes_without_flip",
    "occluded_wasd_follows_forward_camera_intent",
    "front_occluder_retains_real_parallax_aim",
)
CAPTURES = ("occlusion-behind", "occlusion-return", "occlusion-front")
MAP = {
    "schema_version": 1, "kind": "rm_flat_map", "id": "import_lab",
    "display_name": "Camera occlusion native regression fixture", "units": "m",
    "coordinate_system": "ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP",
    "floor": {"size_m": [20, 20], "surface_z_m": 0, "thickness_m": .2,
              "color_rgb": [.12, .15, .19]},
    "boundary": {"height_m": 1.5, "thickness_m": .2}, "obstacles": [],
    "spawns": [{"id": "default", "team": "neutral", "position_m": [0, 0, 0], "yaw_rad": 0}],
    "regions": [],
}


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fingerprint(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": h.hexdigest()}


def snapshot(paths):
    return {key: fingerprint(path) for key, path in sorted(paths.items())}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def inspect_png(path):
    """Validate PNG CRCs and bounded native scanline decompression, without Pillow."""
    raw = path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Invalid PNG signature: " + str(path))
    pos, header, payload, ended = 8, None, [], False
    while pos + 12 <= len(raw):
        size = struct.unpack_from(">I", raw, pos)[0]
        kind, end = raw[pos + 4:pos + 8], pos + size + 12
        if end > len(raw):
            raise ValueError("Truncated PNG chunk")
        data = raw[pos + 8:end - 4]
        if zlib.crc32(kind + data) & 0xffffffff != struct.unpack_from(">I", raw, end - 4)[0]:
            raise ValueError("PNG CRC mismatch")
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", data)
        elif kind == b"IDAT":
            payload.append(data)
        elif kind == b"IEND":
            ended = size == 0
            pos = end
            break
        pos = end
    if not ended or pos != len(raw) or header is None:
        raise ValueError("PNG lacks a complete final IEND")
    width, height, depth, color, compression, filtering, interlace = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    if not channels or depth != 8 or compression or filtering or interlace or not 1 <= min(width, height) <= max(width, height) <= 8192:
        raise ValueError("Unexpected native PNG format")
    stride = width * channels + 1
    decoder = zlib.decompressobj()
    pixels = decoder.decompress(b"".join(payload), stride * height + 1)
    if not decoder.eof or decoder.unused_data or len(pixels) != stride * height or any(v > 4 for v in pixels[::stride]):
        raise ValueError("PNG scanline payload is incomplete")
    return dict(fingerprint(path), width=width, height=height, decoded=True)


def prepare(args):
    platform = "Win64" if sys.platform == "win32" else "Linux"
    if sys.platform not in ("win32", "linux"):
        raise ValueError("Supported native platforms: Windows and Linux")
    if platform == "Linux" and args.renderer in ("d3d11", "d3d12"):
        raise ValueError("D3D requires Windows; select nullrhi or vulkan")
    binary_dir = args.project.parent / "Binaries" / platform
    editor = args.engine_root / "Engine/Binaries" / platform / ("UnrealEditor.exe" if platform == "Win64" else "UnrealEditor")
    module_index = binary_dir / "UnrealEditor.modules"
    names = ["UnrealEditor-RMSim.dll"] if platform == "Win64" else ["libUnrealEditor-RMSim.so", "UnrealEditor-RMSim.so"]
    if module_index.is_file():
        name = load(module_index).get("Modules", {}).get("RMSim")
        if isinstance(name, str) and Path(name).name == name:
            names.insert(0, name)
    module = next((binary_dir / n for n in names if (binary_dir / n).is_file()), None)
    if not editor.is_file() or module is None:
        raise FileNotFoundError("Build the selected RMSim project with this native engine before running")
    source = args.project.parent / "Source/RMSim"
    text = (source / "CameraHeadingProbe.cpp").read_text(encoding="utf-8-sig")
    for token in ("CameraAimOcclusionProbe", "OcclusionNoCapture", "native_camera_aim_occlusion_v1"):
        if token not in text:
            raise ValueError("Required native fixture missing: " + token)
    scene = args.output / "occlusion-map.json"
    save(scene, MAP)
    paths = {"project": args.project, "module": module, "mesh_rig": args.mesh_rig_file,
             "fixture_map": scene, "verifier": Path(__file__).resolve(),
             "engine_version": args.engine_root / "Engine/Build/Build.version"}
    if module_index.is_file():
        paths["module_index"] = module_index
    for file in source.rglob("*"):
        if file.is_file() and file.suffix in (".cpp", ".h", ".cs"):
            paths["source/" + file.relative_to(source).as_posix()] = file
    for file in (args.project.parent / "Config").rglob("*.ini"):
        paths["config/" + file.relative_to(args.project.parent / "Config").as_posix()] = file
    rig = load(args.mesh_rig_file)
    camera = rig.get("camera", {}).get("id", "aim_camera")
    if not isinstance(camera, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", camera):
        raise ValueError("Invalid mesh-rig camera id")
    assets = set()
    for part in rig.get("parts", []):
        for key in ("asset", "collision_asset"):
            asset = part.get(key)
            if isinstance(asset, str) and asset.startswith("/Game/"):
                relative = Path(asset[6:].split(".", 1)[0])
                if ".." in relative.parts:
                    raise ValueError("Invalid native asset path")
                package = args.project.parent / "Content" / relative
                assets.add(package.with_suffix(".uasset"))
                for suffix in (".uexp", ".ubulk"):
                    if package.with_suffix(suffix).is_file():
                        assets.add(package.with_suffix(suffix))
    if not assets or len(assets) > 256:
        raise ValueError("Expected 1..256 installed vehicle asset files")
    for file in sorted(assets):
        paths["asset/" + file.relative_to(args.project.parent / "Content").as_posix()] = file
    return editor, scene, paths, camera


def verify_native(report, expected_regression):
    rows = report.get("checks", [])
    metrics = report.get("phase_metrics", [])
    phase = {m.get("phase"): m for m in metrics if isinstance(m, dict)}
    samples = report.get("samples", [])
    states = [s.get("state", {}) for s in samples]
    states += [r.get(which, {}) for r in rows for which in ("before", "after")]
    names = tuple(r.get("name") for r in rows)
    flags = [r.get("passed") for r in rows]
    first, final = report.get("initial_motion_generation"), report.get("final_motion_generation")
    recoveries = report.get("initial_recovery_count")
    checks = {
        "correct_completed_native_probe": report.get("probe") == "native_camera_aim_occlusion_v1"
            and report.get("camera_occlusion_fixture") is True and report.get("complete") is True,
        "no_reset_or_pose_override": finite(first) and first == final
            and recoveries == report.get("final_recovery_count") == 0
            and report.get("pose_overrides_used") is False and bool(states)
            and all(s.get("contact_physics") is True and s.get("contact_recovery_count") == 0
                    and s.get("probe_world_aim_override") is False for s in states),
        "all_recorded_joint_angles_finite": bool(states) and all(
            finite(s.get("gimbal_yaw_rad")) and finite(s.get("gimbal_pitch_rad")) for s in states),
    }
    raw = []
    for s in samples:
        if s.get("phase") != 2 or s.get("phase_seconds", 0) <= .3:
            continue
        hit, muzzle, view = (s.get(k) for k in ("raw_camera_hit_cm", "muzzle_position_cm", "chase_view_direction"))
        if not all(isinstance(v, list) and len(v) == 3 and all(finite(n) for n in v) for v in (hit, muzzle, view)):
            continue
        length = math.hypot(view[0], view[1])
        if length > 1e-6:
            raw.append(sum((hit[i] - muzzle[i]) * view[i] for i in (0, 1)) / length)
    m = phase.get(2, {})
    checks["independent_native_behind_muzzle_geometry"] = (
        len(raw) >= 3 and max(raw) < -25 and m.get("behind_muzzle_raw_trace_frames", 0) > 30
        and m.get("maximum_legacy_raw_target_yaw_error_deg", 0) > 150)
    if expected_regression:
        checks["exact_expected_old_failure"] = report.get("status") == "failed" and names == CHECKS[:3] and flags == [True, True, False]
        checks["native_old_gimbal_reversal_observed"] = (
            m.get("actual_gimbal_turn_deg", 0) > 150 and m.get("peak_actual_view_yaw_error_deg_after_settle", 0) > 150)
    else:
        checks["exact_ten_native_checks_passed"] = report.get("status") == "passed" and names == CHECKS and flags == [True] * 10
        checks["same_behind_geometry_retains_forward_aim"] = finite(m.get("peak_actual_view_yaw_error_deg_after_settle")) and m["peak_actual_view_yaw_error_deg_after_settle"] < .5
        checks["both_yaw_seams_exercised"] = all(phase.get(p, {}).get("crossed_view_yaw_180_seam") is True for p in (3, 4))
        drive = phase.get(8, {})
        checks["physical_short_drive_matches_view_intent"] = (
            drive.get("intended_drive_frames", 0) > 30 and drive.get("minimum_requested_drive_vs_view_dot", -1) > .9999
            and 1 < drive.get("displacement_cm", -1) < 15)
        checks["forward_target_countercheck_exercised"] = phase.get(9, {}).get("front_muzzle_raw_trace_frames", 0) > 30
    return checks, {"raw_behind_depth_cm": raw, "phase_metrics": metrics,
                    "native_checks": [{k: r.get(k) for k in ("name", "passed", "detail")} for r in rows]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("engine-root", "project", "repo-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--mesh-rig-file", type=Path, help="Default: repo-root/assets/vehicles/aim_sentry/ue_mesh_rig.json")
    parser.add_argument("--renderer", choices=("nullrhi", "d3d11", "d3d12", "vulkan"), default="nullrhi")
    parser.add_argument("--timeout", type=float, default=180.)
    parser.add_argument("--expect-regression", action="store_true", help="Require the exact old-code third-check reversal; records regression_reproduced, never passed")
    args = parser.parse_args(argv)
    if not finite(args.timeout) or not 30 <= args.timeout <= 900:
        parser.error("timeout must be 30..900 wall seconds")
    for name in ("engine_root", "project", "repo_root", "output"):
        setattr(args, name, getattr(args, name).resolve())
    args.mesh_rig_file = (args.mesh_rig_file or args.repo_root / "assets/vehicles/aim_sentry/ue_mesh_rig.json").resolve()
    if args.output.exists():
        parser.error("Use a fresh output directory; previous evidence is preserved")
    args.output.mkdir(parents=True)
    started, process, paths = time.monotonic(), None, {}
    result = {"schema": "rmsim_camera_occlusion_verification_v1", "status": "failed", "started_utc": utc(),
              "renderer": args.renderer, "expected_regression": args.expect_regression,
              "repository_root": str(args.repo_root), "checks": {}, "performance_benchmark": False,
              "os_input_verified": False, "human_visual_review_verified": False,
              "human_visual_review_required": args.renderer != "nullrhi",
              "scope": "native real collision queries, shared manual input, actual Chaos chassis; optional decoded scene captures"}
    try:
        editor, scene, paths, camera = prepare(args)
        before = snapshot(paths)
        save(args.output / "inputs-before.json", before)
        result["module_before"] = before["module"]
        evidence = args.output / "evidence"
        command = [str(editor), str(args.project), "/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode",
                   "-game", "-unattended", "-NoSound", "-NoSplash", "-nop4", "-NoShaderWorker",
                   "-windowed", "-ResX=1280", "-ResY=720", "-GyroProbe", "-CameraHeadingProbe",
                   "-CameraAimOcclusionProbe", "-SimStepSeconds=0.005", "-SpawnId=default",
                   "-MeshRigFile=" + str(args.mesh_rig_file), "-MapFile=" + str(scene),
                   "-EvidenceDir=" + str(evidence), "-abslog=" + str(args.output / "runtime.log")]
        command += ["-nullrhi", "-OcclusionNoCapture"] if args.renderer == "nullrhi" else ["-RenderOffScreen", "-" + args.renderer]
        save(args.output / "command.json", command)
        result["command"] = command
        with (args.output / "stdout.log").open("wb") as stream:
            process = subprocess.Popen(command, cwd=args.project.parent, stdout=stream, stderr=subprocess.STDOUT)
            result["owned_pid"] = process.pid
            result["native_exit_code"] = process.wait(timeout=args.timeout)
        report_path = evidence / "control-result.json"
        native = load(report_path)
        result["checks"], result["observations"] = verify_native(native, args.expect_regression)
        result["native_report"] = fingerprint(report_path)
        result["checks"]["expected_native_exit"] = process.returncode == (2 if args.expect_regression else 0)
        log = (args.output / "runtime.log").read_text(encoding="utf-8-sig", errors="replace")
        fatal = re.findall(r"(?im)^.*(?:Fatal error:|LowLevelFatalError|Unhandled Exception:|Assertion failed:|Critical error:).*$", log)
        result["fatal_log_lines"] = fatal[:20]
        result["checks"]["no_fatal_runtime_error"] = not fatal
        null = args.renderer == "nullrhi"
        result["checks"]["capture_mode_explicit"] = native.get("capture_disabled") is null and native.get("capture_sets_expected") == (0 if null else 3)
        pictures = {}
        if null or args.expect_regression:
            result["checks"]["no_unexpected_captures"] = not list(evidence.rglob("*.png"))
            if not args.expect_regression:
                result["checks"]["zero_capture_sets_written"] = native.get("capture_sets_written") == 0
        else:
            for phase in CAPTURES:
                for name in ("overview", camera):
                    file = evidence / phase / (name + ".png")
                    pictures[phase + "/" + name] = inspect_png(file)
            result["checks"]["three_complete_native_capture_sets"] = native.get("capture_sets_written") == 3 and len(pictures) == 6
            shader_errors = re.findall(r"(?im)^.*(?:Failed to compile Material|Failed to compile global shader|shader compiler errors).*$", log)
            result["shader_error_lines"] = shader_errors[:20]
            result["checks"]["no_shader_compile_errors"] = not shader_errors
        result["decoded_pngs"] = pictures
    except (Exception, KeyboardInterrupt) as error:
        result["error"] = repr(error)
        result["timed_out"] = isinstance(error, subprocess.TimeoutExpired)
    finally:
        if process is not None and process.poll() is None:
            try:
                process.kill()  # Only the exact process launched above; never an image-name kill.
                process.wait(timeout=10)
                result["owned_process_stopped_after_failure"] = True
            except Exception as error:
                result["cleanup_error"] = repr(error)
        result["native_exit_code"] = process.poll() if process else None
        if paths and (args.output / "inputs-before.json").is_file():
            try:
                after = snapshot(paths)
                save(args.output / "inputs-after.json", after)
                result["checks"]["all_module_source_config_and_asset_hashes_unchanged"] = load(args.output / "inputs-before.json") == after
                result["module_after"] = after["module"]
            except Exception as error:
                result["input_verification_error"] = repr(error)
        if result["checks"] and all(result["checks"].values()) and not any(k in result for k in ("error", "cleanup_error", "input_verification_error")):
            result["status"] = "regression_reproduced" if args.expect_regression else "passed"
        result["finished_utc"] = utc()
        result["elapsed_wall_seconds"] = time.monotonic() - started
        save(args.output / "verification.json", result)
    print(json.dumps({"status": result["status"], "report": str(args.output / "verification.json"),
                      "failed_checks": [k for k, v in result["checks"].items() if not v], "error": result.get("error")}))
    return 0 if result["status"] in ("passed", "regression_reproduced") else 2


if __name__ == "__main__":
    raise SystemExit(main())
