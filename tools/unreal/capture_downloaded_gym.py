"""Capture the actual native gym using its SM6 profile and existing collision probe."""
from pathlib import Path
import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
import time


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("D:/simulator"))
    parser.add_argument("--engine-root", type=Path, default=Path("E:/simulator-tools/UE_5.8"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--views", nargs="+", default=["CompleteGym", "DriverView"])
    parser.add_argument("--capture-delay", type=float, default=45)
    parser.add_argument("--timeout", type=float, default=1200)
    args = parser.parse_args()
    root, output = args.project_root.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    project = root / "unreal/RMSim/RMSim.uproject"
    executable = args.engine_root / "Engine/Binaries/Win64/UnrealEditor.exe"
    prepared_path = root / "assets/maps/rmuc2027/arena/downloaded-gym/prepared/prepared.json"
    prepared = json.loads(prepared_path.read_text(encoding="utf-8-sig"))
    cameras = {camera["name"]: camera for camera in prepared["review_cameras"]}
    report = {"status": "running", "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "module_sha256": sha(project.parent / "Binaries/Win64/UnrealEditor-RMSim.dll"),
              "manifest_sha256": sha(root / "assets/maps/rmuc2027/arena-shell.json"),
              "source_map_sha256": sha(root / "assets/maps/rmuc2027/map.json"),
              "vehicle_rig_sha256": sha(root / "assets/vehicles/aim_sentry/ue_mesh_rig.json"),
              "prepared_sha256": sha(prepared_path), "views": []}
    from PIL import Image
    try:
        for name in args.views:
            camera = cameras[name]
            folder = output / name
            folder.mkdir()
            probe_path, log_path = folder / "gym-probe.json", folder / "native.log"
            def ros_vector(values):
                return ",".join(format(v, ".9g") for v in (values[0], -values[1], values[2]))
            command = [str(executable), str(project),
                       "/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode",
                       "-game", "-ContactPhysics", "-RenderOffScreen", "-unattended", "-NoSplash", "-NoSound",
                       "-CustomConfig=RMSimGymLumen", "-d3d12", "-sm6",
                       "-ini:Engine:[DevOptions.Shaders]:bAllowCompilingThroughWorkers=True,[DevOptions.Shaders]:NumUnusedShaderCompilingThreads=20,[DevOptions.Shaders]:NumUnusedShaderCompilingThreadsDuringGame=20,[DevOptions.Shaders]:ShaderCompilerCoreCountThreshold=64",
                       "-MapFile=" + str(root / "assets/maps/rmuc2027/map.json"),
                       "-MeshRigFile=" + str(root / "assets/vehicles/aim_sentry/ue_mesh_rig.json"),
                       "-GymCameraPosition=" + ros_vector(camera["position"]),
                       "-GymCameraTarget=" + ros_vector(camera["target"]),
                       "-GymCameraFov=" + str(camera["horizontal_fov_degrees"]),
                       "-GymProbeOutput=" + str(probe_path), "-CaptureDir=" + str(folder),
                       "-CaptureDelay=" + str(args.capture_delay), "-ExitAfterCapture",
                       "-ResX=1600", "-ResY=1000", "-ForceRes", "-abslog=" + str(log_path),
                       "-ExecCmds=r.Shadow.Virtual.Enable,r.GenerateMeshDistanceFields,r.AntiAliasingMethod,r.Lumen.DiffuseIndirect.Allow,r.Lumen.Reflections.Allow,r.Nanite"]
            (folder / "command.json").write_text(json.dumps(command, indent=2), encoding="utf-8")
            print("START_NATIVE_VIEW", name, flush=True)
            start = time.time()
            with (folder / "process.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, cwd=root)
                (folder / "process-id.txt").write_text(str(process.pid), encoding="ascii")
                try:
                    code = process.wait(timeout=args.timeout)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=30)
                    raise RuntimeError("Owned native capture exceeded timeout: " + name)
            if code != 0:
                raise RuntimeError("Native view exited " + str(code) + ": " + name)
            probe = json.loads(probe_path.read_text(encoding="utf-8-sig"))
            if probe["status"] != "passed" or probe.get("native_mesh_assets_loaded") != 28 or probe.get("native_mesh_instances") != 28:
                raise RuntimeError("Native gym or existing boundary probe failed")
            if not probe.get("native_layout") or probe.get("procedural_parquet_enabled") or probe.get("fixed_wall_branding_enabled"):
                raise RuntimeError("Unexpected legacy geometry was used")
            if probe.get("gi_method_requested") != "lumen" or probe.get("reflection_method_requested") != "lumen":
                raise RuntimeError("Native PostProcess did not select Lumen")
            images = []
            for filename in ("overview.png", "aim_camera.png"):
                path = folder / filename
                with Image.open(path) as bitmap:
                    bitmap.load()
                    if min(bitmap.size) < 256:
                        raise RuntimeError("Capture resolution too small")
                    dimensions = list(bitmap.size)
                images.append({"path": str(path), "dimensions": dimensions, "sha256": sha(path)})
            text = log_path.read_text(encoding="utf-8-sig", errors="replace")
            if "RMSIM_GYM_CAMERA_INVALID" in text or "RMSIM_GYM_CAMERA_CUSTOM" not in text:
                raise RuntimeError("Requested native review camera did not activate")
            if "PCD3D_SM6" not in text:
                raise RuntimeError("SM6 not confirmed in native log")
            evidence = [line for line in text.splitlines() if any(term in line for term in
                        ("PCD3D_SM6", "r.Shadow.Virtual.Enable", "r.GenerateMeshDistanceFields", "r.AntiAliasingMethod",
                         "r.Lumen.DiffuseIndirect.Allow", "r.Lumen.Reflections.Allow", "r.Nanite =", "RMSIM_CAMPUS_LIGHTING_PP"))]
            report["views"].append({"name": name, "exit_code": code, "seconds": round(time.time()-start, 2),
                                    "probe": str(probe_path), "images": images,
                                    "renderer_evidence": evidence, "visual_review": "pending_human_image_inspection"})
            (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print("NATIVE_VIEW_CAPTURED", name, "probe=passed", flush=True)
        report["status"] = "native_capture_passed_pending_visual_review"
    except Exception as error:
        report.update(status="failed", error=str(error))
        raise
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
