"""Independent external sentry_ws navigation connector (Point-LIO + Nav2 + RViz2).

Connects purely over ROS 2 to an already-running Pure Simulator + rmsim_bridge
without owning or restarting UE5.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/integration"))
sys.path.insert(0, str(ROOT / "ros2_ws/src/rmsim_bridge"))

from navigation_console_config import prepare_scene, rviz_configuration, scene_spawn_prior  # noqa: E402
from native_sentry_behavior import prepare_behavior  # noqa: E402
from rmsim_bridge.navigation_map_identity import validate_localization_map  # noqa: E402
from rmsim_bridge.native_transport import configure_navigation_transport  # noqa: E402
from rmsim_bridge.protocol import identifier  # noqa: E402


def prepare_navigation_inputs(args, session_dir: Path) -> tuple[Path, str, list[float]]:
    map_file = args.map_file.resolve()
    packaged_map = ROOT / "assets/maps/rmuc2027/navigation-console-platform-support-v2/map.yaml"
    if args.map_yaml is None or args.map_yaml.resolve() == packaged_map.resolve():
        scene = prepare_scene("rmuc2027", session_dir / "scene", args.localization_map_yaml,
                              runtime_scene=map_file, spawn_id=args.spawn_id)
        map_yaml = Path(scene["map_yaml"])
        localization_map = scene["localization_map_yaml"]
        initial_pose = scene["initial_pose"]
    else:
        map_yaml = args.map_yaml.resolve(strict=True)
        localization_map = str(args.localization_map_yaml.resolve(strict=True)) if args.localization_map_yaml else ""
        scene_bytes = map_file.read_bytes()
        scene = json.loads(scene_bytes.decode("utf-8-sig"))
        metadata = json.loads(map_yaml.with_name("metadata.json").read_text(encoding="utf-8-sig"))
        if (metadata.get("map_id") != scene["id"]
                or str(metadata.get("source_md5", "")).lower() != hashlib.md5(scene_bytes).hexdigest()):
            raise ValueError("Planning map metadata does not match the simulator map file")
        initial_pose, _ = scene_spawn_prior(scene, spawn_id=args.spawn_id)
    if localization_map:
        validate_localization_map(map_yaml, localization_map)
    return map_yaml, localization_map, initial_pose


def main() -> int:
    parser = argparse.ArgumentParser(description="Attach external sentry_ws (Point-LIO + Nav2 + RViz2) to a running simulator.")
    parser.add_argument("--robot-id", default="aim_sentry")
    parser.add_argument("--domain", type=int, default=71)
    parser.add_argument(
        "--navigation-source",
        type=Path,
        default=ROOT / "ros2_ws/src/pb2025_nav_bringup/config/simulation/nav2_params.yaml",
    )
    parser.add_argument("--map-file", type=Path, default=ROOT / "assets/maps/rmuc2027/map.json",
                        help="Exact map manifest loaded by the simulator")
    parser.add_argument("--spawn-id", default="default", help="Spawn selected in the simulator")
    parser.add_argument("--map-yaml", type=Path, help="Explicit planning grid with adjacent metadata.json")
    parser.add_argument("--localization-map-yaml", type=Path, help="Override the packaged localization grid")
    parser.add_argument(
        "--drive-profile",
        type=Path,
        default=ROOT / "assets/vehicles/aim_sentry/drive-profile.json",
    )
    parser.add_argument("--no-rviz", action="store_true")
    parser.add_argument("--match-autonomy", action="store_true")
    parser.add_argument("--no-behavior", action="store_true", help="Disable automatic RMUC 2027 sentry behavior tree node")
    parser.add_argument("--no-vision", action="store_true", help="Disable automatic vision auto-aim node")
    parser.add_argument(
        "--vision-backend",
        choices=("sp_vision_25", "legacy"),
        default="sp_vision_25",
        help="Vision auto-aim backend to launch in --match-autonomy mode",
    )
    args = parser.parse_args()

    os.environ["ROS_DOMAIN_ID"] = str(args.domain)
    configure_navigation_transport()
    env = dict(os.environ)

    ros2 = shutil.which("ros2")
    if not ros2:
        print("[ERROR] ros2 executable not found in PATH; invoke via tools/environment/run-env.ps1", flush=True)
        return 2

    session_dir = ROOT / "artifacts/navigation" / (
        "external-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"
    )
    ref_config = ROOT / "ros2_ws/src/pb2025_nav_bringup/config/reality/nav2_params.yaml"
    native_runtime = ROOT / "artifacts/runtime/navigation/windows"
    rviz_exe = ROOT / "artifacts/runtime/rviz/windows/rviz2.exe"
    classifier_path = ROOT / "artifacts/runtime/vision/tiny_resnet.onnx"
    sp_vision_assets = ROOT / "ros2_ws/src/sp_vision_25/assets"
    if args.match_autonomy and not args.no_vision:
        if args.vision_backend == "sp_vision_25":
            missing = [f for f in ("yolov8.xml", "yolov8.bin", "tiny_resnet.onnx") if not (sp_vision_assets / f).is_file()]
            if missing:
                print(f"[external_sentry] Missing sp_vision_25 weights under {sp_vision_assets}: {missing}", file=sys.stderr, flush=True)
                return 2
        elif not classifier_path.is_file():
            print(f"[external_sentry] Missing RMUC armor classifier: {classifier_path}", file=sys.stderr, flush=True)
            return 2
    if not args.no_rviz and not rviz_exe.is_file():
        print(f"[ERROR] RViz2 binary missing: {rviz_exe}", flush=True)
        return 3
    try:
        identifier(args.robot_id)
        map_yaml, localization_map, initial_pose = prepare_navigation_inputs(args, session_dir)
        native_behavior = (prepare_behavior(ROOT, session_dir, args.map_file, args.spawn_id, args.robot_id)
                           if args.match_autonomy and not args.no_behavior else None)
        lio_dir = session_dir / "pointlio"
        nav_dir = session_dir / "navigation"
        lio_dir.mkdir(parents=True, exist_ok=True)
        nav_dir.mkdir(parents=True, exist_ok=True)
        rviz_cfg = session_dir / "external-sentry.rviz"
        if not args.no_rviz:
            view = {"x": initial_pose[0], "y": initial_pose[1], "scale": 65.0}
            rviz = rviz_configuration(view, args.robot_id)
            rviz_cfg.write_text(json.dumps(rviz, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError) as error:
        print(f"[external_sentry] Could not prepare navigation: {error}", file=sys.stderr, flush=True)
        return 2

    print(f"[external_sentry] Session dir : {session_dir}", flush=True)
    print(f"[external_sentry] RobotId     : {args.robot_id} (ROS_DOMAIN_ID={args.domain})", flush=True)
    print(f"[external_sentry] Nav YAML    : {args.navigation_source}", flush=True)
    print(f"[external_sentry] Map YAML    : {map_yaml}", flush=True)
    print(f"[external_sentry] Loc Map YAML: {localization_map or map_yaml}", flush=True)
    print(f"[external_sentry] Spawn       : {args.spawn_id} {initial_pose}", flush=True)

    procs: list[tuple[str, subprocess.Popen, object]] = []

    def spawn(name: str, cmd: list[str], proc_env: dict[str, str]):
        log_file = (session_dir / f"{name}.log").open("w", encoding="utf-8")
        p = subprocess.Popen(cmd, cwd=str(ROOT), env=proc_env, stdout=log_file, stderr=subprocess.STDOUT)
        procs.append((name, p, log_file))
        print(f"[external_sentry] Started {name} (PID {p.pid}) -> {session_dir / (name + '.log')}", flush=True)
        return p

    try:
        spawn(
            "pointlio",
            [
                sys.executable,
                "-c",
                "from rmsim_bridge.point_lio_runner import main; main()",
                "--ros-args",
                "-p",
                f"robot_id:={args.robot_id}",
                "-p",
                "imu_id:=mid360_imu",
                "-p",
                f"sentry_source:={args.navigation_source.resolve()}",
                "-p",
                f"output_directory:={lio_dir}",
            ],
            env,
        )

        spawn(
            "navigation",
            [
                sys.executable,
                "-c",
                "from rmsim_bridge.navigation_runner import main; main()",
                "--ros-args",
                "-p",
                f"robot_id:={args.robot_id}",
                "-p",
                f"reference_config:={ref_config}",
                "-p",
                f"map_yaml:={map_yaml}",
                *(["-p", f"localization_map_yaml:={localization_map}"] if localization_map else []),
                "-p",
                f"initial_pose:={json.dumps(initial_pose)}",
                "-p",
                "control_profile:=simulation",
                "-p",
                f"control_source:={args.navigation_source.resolve()}",
                "-p",
                f"drive_profile:={args.drive_profile.resolve()}",
                *(["-p", "match_translation_limit_mps:=4.5"] if args.match_autonomy else []),
                *(["-p", "native_match_behavior:=true"] if native_behavior else []),
                "-p",
                f"native_runtime:={native_runtime}",
                "-p",
                f"output_directory:={nav_dir}",
            ],
            env,
        )

        if native_behavior:
            server, client = native_behavior
            spawn("behavior_server", server, env)
            spawn("behavior_client", client, env)

        if args.match_autonomy and not args.no_vision:
            vis_env = dict(env)
            if args.vision_backend == "sp_vision_25":
                sp_src = str(ROOT / "ros2_ws/src/sp_vision_25")
                vis_env["PYTHONPATH"] = sp_src + (os.pathsep + vis_env["PYTHONPATH"] if vis_env.get("PYTHONPATH") else "")
                sp_bins = [
                    str(ROOT / "ros2_ws/install_control/sp_msgs/bin"),
                    str(ROOT / "ros2_ws/install_control/pb_rm_interfaces/bin"),
                ]
                vis_env["PATH"] = os.pathsep.join(sp_bins) + os.pathsep + vis_env.get("PATH", "")
                spawn(
                    "vision_autoaim",
                    [
                        sys.executable,
                        "-c",
                        "from sp_vision_25.vision_node import main; main()",
                        "--ros-args",
                        "-p",
                        "use_sim_time:=true",
                        "-p",
                        f"robot_id:={args.robot_id}",
                        "-p",
                        f"assets_dir:={sp_vision_assets}",
                        "-p",
                        "require_behavior_switch:=true",
                    ],
                    vis_env,
                )
            else:
                bt_src = str(ROOT / "ros2_ws/src/rmsim_sentry_behavior")
                vis_env["PYTHONPATH"] = bt_src + (os.pathsep + vis_env["PYTHONPATH"] if vis_env.get("PYTHONPATH") else "")
                spawn(
                    "vision_autoaim",
                    [
                        sys.executable,
                        "-c",
                        "from rmsim_sentry_behavior.vision_autoaim_node import main; main()",
                        "--ros-args",
                        "-p",
                        "use_sim_time:=true",
                        "-p",
                        f"robot_id:={args.robot_id}",
                        "-p",
                        f"classifier_path:={classifier_path}",
                    ],
                    vis_env,
                )

        if not args.no_rviz:
            gui_env = dict(env)
            conda_prefix = os.environ.get("CONDA_PREFIX", "")
            if conda_prefix:
                lib = Path(conda_prefix) / "Library"
                gui_env["QT_PLUGIN_PATH"] = str(lib / "plugins")
                gui_env["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(lib / "plugins/platforms")
            rviz_proc = spawn(
                "rviz2",
                [str(rviz_exe), "-d", str(rviz_cfg), "--ros-args", "-p", "use_sim_time:=true"],
                gui_env,
            )
            print("[external_sentry] RViz2 opened! Wait for localization/map, then use Nav2 Goal. Close RViz2 to detach.", flush=True)
            while True:
                if rviz_proc.poll() is not None:
                    print(f"[external_sentry] RViz2 closed (code={rviz_proc.returncode}); detaching external stack.", flush=True)
                    break
                for name, p, _ in procs:
                    if name != "rviz2" and p.poll() is not None:
                        print(f"[external_sentry] Warning: {name} exited (code={p.returncode}). Check {session_dir / (name + '.log')}", flush=True)
                        return p.returncode or 1
                time.sleep(0.5)
        else:
            print("[external_sentry] Running headless (--no-rviz). Press Ctrl+C to detach.", flush=True)
            while True:
                for name, p, _ in procs:
                    if p.poll() is not None:
                        return p.returncode or 1
                time.sleep(0.5)
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        for _name, p, log_file in procs:
            if p.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill.exe", "/PID", str(p.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW, check=False)
                else:
                    p.terminate()
        for _name, p, log_file in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
            try:
                log_file.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
