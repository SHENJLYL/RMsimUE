"""Verify the campus gym in native UE, preserving one evidence folder per view.

This checks scene creation, closed-boundary queries, materials and PNG integrity.
Vehicle driving and human visual review have separate evidence requirements.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
import traceback
import zlib

VIEWS = ("hero", "field", "gate", "reverse")
NATIVE_EXTENSIONS = {".uasset", ".uexp", ".ubulk"}


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def file_record(path):
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError("Expected file: " + str(path))
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def package_file(parent, value):
    if not isinstance(value, str) or not value or ".." in value or Path(value).is_absolute():
        raise ValueError("Invalid package-relative path: " + repr(value))
    return parent / value


def locate_inputs(args):
    if load_json(args.map_file).get("id") != "rmuc2027":
        raise ValueError("The campus gym verifier requires the rmuc2027 map")
    manifest = args.map_file.parent / "arena-shell.json"
    if not manifest.is_file():
        for parent in list(args.map_file.parents)[:8]:
            candidate = parent / "assets/maps/rmuc2027/arena-shell.json"
            if candidate.is_file():
                manifest = candidate
                break
    data = load_json(manifest)
    if data.get("schema") != "rmsim_campus_gym_v1":
        raise ValueError("Expected the campus gym manifest, not the legacy arena shell")
    layout = package_file(manifest.parent, data.get("layout"))
    source = package_file(manifest.parent, data.get("source", "arena/campus-gym/SOURCE.json"))
    repo = next((parent for parent in args.project.parents
                 if (parent / "tools/unreal").is_dir() and
                 (parent / "assets/maps/rmuc2027").is_dir()), None)
    source_fallback = False
    if not source.is_file() and repo:
        source = repo / "assets/maps/rmuc2027/arena/campus-gym/SOURCE.json"
        source_fallback = True
    platform = "Win64" if sys.platform == "win32" else "Linux"
    if sys.platform not in ("win32", "linux"):
        raise ValueError("Native verification supports Windows and Linux")
    editor = args.engine_root / "Engine/Binaries" / platform / ("UnrealEditor.exe" if platform == "Win64" else "UnrealEditor")
    binary_dir = args.project.parent / "Binaries" / platform
    module_candidates = [binary_dir / name for name in
                         (("UnrealEditor-RMSim.dll",) if platform == "Win64" else
                          ("libUnrealEditor-RMSim.so", "UnrealEditor-RMSim.so"))]
    module_index = binary_dir / "UnrealEditor.modules"
    if module_index.is_file():
        name = load_json(module_index).get("Modules", {}).get("RMSim")
        if isinstance(name, str) and Path(name).name == name:
            module_candidates.insert(0, binary_dir / name)
    module = next((path for path in module_candidates if path.is_file()), None)
    if module is None:
        raise FileNotFoundError("Build the native RMSim editor module before verification: " + str(binary_dir))
    required = {"project": args.project, "map": args.map_file, "mesh_rig": args.mesh_rig_file,
                "arena_manifest": manifest, "layout": layout, "source_manifest": source,
                "module": module, "verifier_script": Path(__file__).resolve()}
    source_dir = args.project.parent / "Source/RMSim"
    for name in ("CampusGym.cpp", "CampusGym.h", "ArenaShell.cpp", "ArenaShell.h",
                 "ImportLabGameMode.cpp", "ImportLabGameMode.h"):
        required["source/" + name] = source_dir / name
    for name in ("DefaultEngine.ini", "DefaultGame.ini"):
        path = args.project.parent / "Config" / name
        if path.is_file():
            required["config/" + name] = path
    if repo:
        for relative in ("tools/maps/prepare_campus_gym_assets.py", "tools/maps/prepare_campus_seat_row.py",
                         "tools/maps/build_campus_gym_layout.py", "tools/unreal/setup_campus_gym.py",
                         "tools/unreal/import_campus_gym_assets.py"):
            if (repo / relative).is_file():
                required["setup/" + relative] = repo / relative
    if not editor.is_file():
        raise FileNotFoundError("Native UE executable missing: " + str(editor))
    native_dir = args.project.parent / "Content/RMSim/Maps/rmuc2027/Arena/Campus"
    return editor, required, native_dir, {"platform": platform,
        "repository_root": str(repo) if repo else None, "source_manifest_fallback": source_fallback}


def snapshot(required, native_dir):
    files = sorted(path for path in native_dir.rglob("*")
                   if path.is_file() and path.suffix.lower() in NATIVE_EXTENSIONS)
    if not 1 <= len(files) <= 100:
        raise ValueError("Expected 1..100 campus native asset files; found " + str(len(files)))
    return {"inputs": {key: file_record(path) for key, path in required.items()},
            "native_assets": {path.relative_to(native_dir).as_posix(): file_record(path) for path in files}}


def inspect_png(path):
    """Check actual native PNG chunks, CRCs and decompressed scanline payload."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Invalid PNG signature")
    pos, header, compressed, ended = 8, None, [], False
    while pos + 12 <= len(data):
        size = struct.unpack_from(">I", data, pos)[0]
        kind = data[pos + 4:pos + 8]
        end = pos + 12 + size
        if end > len(data):
            raise ValueError("Truncated PNG chunk")
        payload = data[pos + 8:pos + 8 + size]
        if zlib.crc32(kind + payload) & 0xffffffff != struct.unpack_from(">I", data, pos + 8 + size)[0]:
            raise ValueError("PNG chunk CRC mismatch")
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            compressed.append(payload)
        elif kind == b"IEND":
            ended = size == 0
            pos = end
            break
        pos = end
    if not ended or pos != len(data) or header is None:
        raise ValueError("Incomplete PNG")
    width, height, depth, color, compression, filtering, interlace = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    if not channels or depth != 8 or compression or filtering or interlace or not (1 <= width <= 8192 and 1 <= height <= 8192):
        raise ValueError("Unexpected native PNG format")
    stride = width * channels + 1
    decoder = zlib.decompressobj()
    pixels = decoder.decompress(b"".join(compressed), stride * height + 1)
    if not decoder.eof or decoder.unused_data or len(pixels) != stride * height or any(v > 4 for v in pixels[::stride]):
        raise ValueError("Invalid PNG scanline payload")
    return dict(file_record(path), width=width, height=height, decoded=True)


def run_view(args, editor, view):
    out = args.output / view
    out.mkdir(exist_ok=False)
    captures = out / "captures"
    command = [str(editor), str(args.project), "/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode",
        "-game", "-ContactPhysics", "-RenderOffScreen", "-unattended", "-NoShaderWorker", "-NoSplash", "-NoSound",
        "-MapFile=" + str(args.map_file), "-MeshRigFile=" + str(args.mesh_rig_file),
        "-GymView=" + view, "-GymProbeOutput=" + str(out / "boundary-report.json"),
        "-CaptureDir=" + str(captures), "-CaptureDelay=8", "-ExitAfterCapture",
        "-ResX=1600", "-ResY=1000", "-ForceRes", "-abslog=" + str(out / "runtime.log")]
    write_json(out / "command.json", command)
    result = {"view": view, "command": command, "started_utc": utc(), "timeout": False,
              "exit_code": None, "human_visual_review_required": True}
    start = time.monotonic()
    with (out / "stdout.log").open("wb") as stdout:
        try:
            proc = subprocess.run(command, cwd=args.project.parent, stdout=stdout,
                                  stderr=subprocess.STDOUT, timeout=args.timeout)
            result["exit_code"] = proc.returncode
        except subprocess.TimeoutExpired:
            result["timeout"] = True
        except OSError as error:
            result["launch_error"] = str(error)
    result["elapsed_wall_s"] = time.monotonic() - start
    result["finished_utc"] = utc()
    log_path = out / "runtime.log"
    log = log_path.read_text(encoding="utf-8-sig", errors="replace") if log_path.is_file() else ""
    gate_path = out / "boundary-report.json"
    try:
        gate = load_json(gate_path) if gate_path.is_file() else {}
    except (OSError, ValueError) as error:
        gate = {}
        result["native_report_error"] = str(error)
    fatal = re.findall(r"(?im)^.*(?:Fatal error:|LowLevelFatalError|Unhandled Exception:|Assertion failed:|Critical error:).*$", log)
    shader = re.findall(r"(?im)^.*(?:Failed to compile Material|Failed to compile global shader|shader compiler errors).*$", log)
    attachment = re.findall(r"(?im)^.*AttachTo:.*$", log)
    result["log_findings"] = {"fatal": fatal[:20], "shader": shader[:20], "attachment": attachment[:20]}
    pictures, image_errors = {}, {}
    for name in ("overview.png", "aim_camera.png"):
        try:
            pictures[name] = inspect_png(captures / name)
        except (OSError, ValueError, struct.error, zlib.error) as error:
            image_errors[name] = str(error)
    checks = {
        "process_exit_zero": result["exit_code"] == 0 and not result["timeout"],
        "native_report_passed": gate.get("status") == "passed",
        "all_96_closed_boundary_sweeps": gate.get("sweep_samples") == 96 and gate.get("sweep_passed") == 96,
        "four_continuous_collision_walls": gate.get("continuous_collision_walls") == 4,
        "four_fixed_gate_leaves": gate.get("fixed_gate_leaves") == 4,
        "no_gate_interaction": gate.get("gate_interaction_enabled") is False,
        "glass_translucent": gate.get("glass_material_blend") == "translucent",
        "twelve_indoor_rect_lights": gate.get("indoor_rect_lights") == 12,
        "cosmetic_no_collision": gate.get("cosmetic_no_collision") is True,
        "gym_loaded": "RMSIM_CAMPUS_GYM_READY" in log,
        "no_shader_compile_errors": not shader,
        "no_fatal_errors": not fatal,
        "no_invalid_attachment": not attachment,
        "two_native_pngs_decoded": len(pictures) == 2 and not image_errors,
    }
    result.update(status="passed" if all(checks.values()) else "failed", checks=checks,
                  screenshots=pictures, image_errors=image_errors,
                  boundary_report=file_record(gate_path) if gate_path.is_file() else None)
    write_json(out / "verification.json", result)
    print(json.dumps({"view": view, "status": result["status"], "elapsed_wall_s": result["elapsed_wall_s"]}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("engine-root", "project", "map-file", "mesh-rig-file", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--views", choices=VIEWS, nargs="+", default=list(VIEWS))
    parser.add_argument("--timeout", type=float, default=420.0, help="Per-view process timeout in seconds (default 420)")
    args = parser.parse_args()
    if not 10 <= args.timeout <= 1800 or len(set(args.views)) != len(args.views):
        parser.error("Use unique views and a finite timeout between 10 and 1800 seconds")
    for name in ("engine_root", "project", "map_file", "mesh_rig_file", "output"):
        setattr(args, name, getattr(args, name).resolve())
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"status": "running", "started_utc": utc(), "views": [],
        "scope": "Native campus-gym scene, closed-boundary sphere sweeps, material metadata and PNG integrity",
        "human_visual_review_required": True, "human_visual_review_status": "pending",
        "physical_vehicle_drive_test": False, "performance_benchmark": False}
    start = time.monotonic()
    try:
        editor, required, native_dir, context = locate_inputs(args)
        report.update(context)
        report["inputs_before"] = snapshot(required, native_dir)
        write_json(args.output / "verification.json", report)
        for view in args.views:
            report["views"].append(run_view(args, editor, view))
        report["inputs_after"] = snapshot(required, native_dir)
        report["checks"] = {"all_requested_views_passed": len(report["views"]) == len(args.views) and
                            all(item["status"] == "passed" for item in report["views"]),
                            "module_sources_manifests_assets_unchanged": report["inputs_before"] == report["inputs_after"]}
        report["status"] = "passed" if all(report["checks"].values()) else "failed"
    except Exception as error:
        report.update(status="failed", error=str(error), traceback=traceback.format_exc())
    finally:
        report.update(finished_utc=utc(), elapsed_wall_s=time.monotonic() - start)
        write_json(args.output / "verification.json", report)
    print(json.dumps({"status": report["status"], "output": str(args.output),
                      "human_visual_review_required": True}), flush=True)
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
