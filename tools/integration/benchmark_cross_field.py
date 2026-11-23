"""Repeatable native Windows RMUC cross-field benchmark (7.86, -6.78) -> (-8.32, 6.16).

Starts navigation_console.py with the chosen navigation YAML, waits for AMCL/Nav2
readiness, sends NavigateToPose to (-8.32, 6.16), records ground truth + static
footprint clearance + controller turntrace stats, and stops cleanly.
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_pgm(pgm_path: Path) -> tuple[int, int, bytes]:
    raw = pgm_path.read_bytes()
    idx = 0
    tokens: list[bytes] = []
    while len(tokens) < 4:
        while idx < len(raw) and raw[idx] in b" \t\r\n":
            idx += 1
        if raw[idx : idx + 1] == b"#":
            while idx < len(raw) and raw[idx] != 10:
                idx += 1
            continue
        start = idx
        while idx < len(raw) and raw[idx] not in b" \t\r\n":
            idx += 1
        tokens.append(raw[start:idx])
    idx += 1  # single whitespace after maxval
    assert tokens[0] == b"P5", f"Expected P5 PGM, got {tokens[0]}"
    w, h = int(tokens[1]), int(tokens[2])
    return w, h, raw[idx : idx + w * h]


def check_static_clearance(map_yaml: Path, trace: list[list[float]], half_size: float = 0.26) -> dict:
    import yaml

    meta = yaml.safe_load(map_yaml.read_text(encoding="utf-8"))
    res = float(meta["resolution"])
    ox, oy = float(meta["origin"][0]), float(meta["origin"][1])
    w, h, data = load_pgm(map_yaml.parent / meta["image"])
    occ_thresh = float(meta.get("occupied_thresh", 0.65))
    # In ROS map_server (negate=0), occ = (255 - pixel) / 255.0
    max_free_pixel = int(round((1.0 - occ_thresh) * 255.0))
    radius = math.hypot(half_size, half_size)
    rad_cells = int(math.ceil(radius / res))

    occupied_poses = 0
    for _t, x, y in trace:
        cx = int(math.floor((x - ox) / res))
        cy_ros = int(math.floor((y - oy) / res))
        hit = False
        for dy in range(-rad_cells, rad_cells + 1):
            for dx in range(-rad_cells, rad_cells + 1):
                if math.hypot(dx * res, dy * res) > radius:
                    continue
                gx = cx + dx
                gy_ros = cy_ros + dy
                if gx < 0 or gx >= w or gy_ros < 0 or gy_ros >= h:
                    hit = True
                    break
                row = h - 1 - gy_ros
                if data[row * w + gx] <= max_free_pixel:
                    hit = True
                    break
            if hit:
                break
        if hit:
            occupied_poses += 1
    return {"checked_poses": len(trace), "occupied_footprints": occupied_poses}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--navigation-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--goal-x", type=float, default=-8.32)
    parser.add_argument("--goal-y", type=float, default=6.16)
    parser.add_argument("--domain", type=int, default=71)
    parser.add_argument("--port", type=int, default=9451)
    parser.add_argument("--visible", action="store_true", help="Show UE5 window and RViz during benchmark")
    args = parser.parse_args()

    out = args.output.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    os.environ["ROS_DOMAIN_ID"] = str(args.domain)
    sys.path.insert(0, str(ROOT / "ros2_ws/src/rmsim_bridge"))
    from rmsim_bridge.native_transport import configure_navigation_transport

    configure_navigation_transport()

    console_cmd = [
        sys.executable,
        str(ROOT / "tools/integration/navigation_console.py"),
        "--scene",
        "rmuc2027",
        "--engine-root",
        r"E:\simulator-tools\UE_5.8",
        "--physics-backend",
        "mujoco",
        "--sim-step",
        "0.005",
        "--pace-real-time",
        *([] if args.visible else ["--no-rviz", "--offscreen"]),
        "--port",
        str(args.port),
        "--domain",
        str(args.domain),
        "--navigation-source",
        str(args.navigation_source.resolve()),
        "--output",
        str(out),
    ]
    child_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_")}
    console_log = (out.parent / f"{out.name}-console.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(console_cmd, cwd=str(ROOT), env=child_env, stdout=console_log, stderr=subprocess.STDOUT)

    import rclpy
    from rclpy.action import ActionClient
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from geometry_msgs.msg import PoseStamped
    from nav2_msgs.action import NavigateToPose

    rclpy.init()
    node = Node("rmsim_cross_field_benchmark")
    trace: list[list[float]] = []
    latest_pose: list[float] | None = None
    max_tilt_deg = 0.0
    recording = False

    def on_truth(msg: PoseStamped):
        nonlocal latest_pose, max_tilt_deg
        t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        latest_pose = [t, msg.pose.position.x, msg.pose.position.y]
        if recording:
            trace.append(latest_pose)
            q = msg.pose.orientation
            up_z = max(-1.0, min(1.0, 1.0 - 2.0 * (q.x * q.x + q.y * q.y)))
            tilt = math.degrees(math.acos(up_z))
            if tilt > max_tilt_deg:
                max_tilt_deg = tilt

    node.create_subscription(PoseStamped, "/rmsim/evaluation/aim_sentry/ground_truth", on_truth, qos_profile_sensor_data)
    client = ActionClient(node, NavigateToPose, "navigate_to_pose")

    try:
        # 1. Wait for navigation_console status == ready
        status_file = out / "status.json"
        deadline = time.monotonic() + 180.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"navigation_console exited early ({proc.returncode}); check {out.parent / (out.name + '-console.log')}")
            if status_file.is_file():
                try:
                    st = json.loads(status_file.read_text(encoding="utf-8"))
                    if st.get("phase") == "ready":
                        break
                except Exception:
                    pass
            rclpy.spin_once(node, timeout_sec=0.1)
        else:
            raise TimeoutError("Timed out waiting for navigation_console ready")

        if not client.wait_for_server(timeout_sec=15.0):
            raise TimeoutError("navigate_to_pose action server unavailable")

        # Wait for fresh ground truth
        t_wait = time.monotonic() + 10.0
        while latest_pose is None and time.monotonic() < t_wait:
            rclpy.spin_once(node, timeout_sec=0.05)
        assert latest_pose is not None

        def send_and_wait(gx: float, gy: float, record_trace: bool):
            nonlocal recording
            goal_msg = NavigateToPose.Goal()
            goal_msg.pose.header.frame_id = "aim_sentry/map"
            goal_msg.pose.pose.position.x = gx
            goal_msg.pose.pose.position.y = gy
            goal_msg.pose.pose.orientation.w = 1.0
            if record_trace:
                trace.clear()
                recording = True
            ws = time.monotonic()
            ss = latest_pose[0]
            sf = client.send_goal_async(goal_msg)
            while not sf.done():
                rclpy.spin_once(node, timeout_sec=0.02)
            gh = sf.result()
            if not gh or not gh.accepted:
                raise RuntimeError(f"NavigateToPose ({gx},{gy}) rejected")
            rf = gh.get_result_async()
            dl = time.monotonic() + 120.0
            while not rf.done():
                if time.monotonic() > dl:
                    raise TimeoutError(f"NavigateToPose ({gx},{gy}) timed out")
                rclpy.spin_once(node, timeout_sec=0.02)
            wt = time.monotonic() - ws
            st_s = (latest_pose[0] - ss) if latest_pose else 0.0
            if record_trace:
                recording = False
            return int(rf.result().status), wt, st_s

        # Seed to exact benchmark start (7.86, -6.78) with slight +Y bias so AMCL doesn't park on south edge
        if math.hypot(latest_pose[1] - 7.86, latest_pose[2] - (-6.78)) > 0.5:
            seed_status, seed_w, _ = send_and_wait(7.86, -6.60, record_trace=False)
            print(f"Seeded to (7.86, -6.60): status={seed_status} wall={seed_w:.2f}s pos=({latest_pose[1]:.2f},{latest_pose[2]:.2f})", flush=True)
            if seed_status != 4:
                raise RuntimeError(f"Failed to reach seed start (7.86, -6.60): status={seed_status}")

        status_code, wall_s, sim_s = send_and_wait(args.goal_x, args.goal_y, record_trace=True)

        end_x, end_y = latest_pose[1], latest_pose[2]
        endpoint_err = math.hypot(end_x - args.goal_x, end_y - args.goal_y)

        dist = 0.0
        max_v = 0.0
        for i in range(1, len(trace)):
            dt = trace[i][0] - trace[i - 1][0]
            ds = math.hypot(trace[i][1] - trace[i - 1][1], trace[i][2] - trace[i - 1][2])
            dist += ds
            if dt > 1e-4:
                max_v = max(max_v, ds / dt)

        clearance = check_static_clearance(
            ROOT / "assets/maps/rmuc2027/navigation-console-platform-support-v2/map.yaml",
            trace,
        )

        # Count turntrace events
        coast, cap = 0, 0
        for lf in (out / "navigation").rglob("01-controller_server.log"):
            txt = lf.read_text(encoding="utf-8", errors="replace")
            coast += txt.count("turntrace coast")
            cap += txt.count("turntrace cap")

        summary = {
            "status_code": status_code,
            "succeeded": status_code == 4,
            "wall_s": round(wall_s, 3),
            "sim_s": round(sim_s, 3),
            "distance_m": round(dist, 2),
            "avg_speed_mps": round(dist / max(sim_s, 1e-6), 2),
            "max_speed_mps": round(max_v, 2),
            "endpoint_xy": [round(end_x, 3), round(end_y, 3)],
            "endpoint_error_m": round(endpoint_err, 4),
            "max_tilt_deg": round(max_tilt_deg, 2),
            "clearance": clearance,
            "turntrace_coast": coast,
            "turntrace_cap": cap,
            "passed_30s": (status_code == 4 and wall_s < 30.0 and endpoint_err < 0.25 and clearance["occupied_footprints"] == 0 and max_tilt_deg < 32.0),
            "passed_26s": (status_code == 4 and wall_s < 26.0 and endpoint_err < 0.25 and clearance["occupied_footprints"] == 0),
        }
        (out / "benchmark-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2), flush=True)
        return 0 if (status_code == 4 and endpoint_err < 0.25 and clearance["occupied_footprints"] == 0) else 1
    finally:
        try:
            (out / "stop.request").write_text("benchmark_done\n", encoding="utf-8")
        except Exception:
            pass
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.terminate()
        try:
            console_log.close()
        except Exception:
            pass
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
