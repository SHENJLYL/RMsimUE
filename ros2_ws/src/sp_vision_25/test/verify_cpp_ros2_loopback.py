"""End-to-end ROS 2 DDS loopback verification for the compiled C++17 sp_vision_25 vision_node."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import cv2
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from geometry_msgs.msg import PoseWithCovarianceStamped
from sensor_msgs.msg import Image
from std_msgs.msg import Int32, String

ROOT = Path(r"D:\simulator")
for _overlay, _pkgs in (
    ("install_control", ("sp_msgs", "pb_rm_interfaces")),
    ("install_behavior", ("dji_referee_protocol",)),
):
    for _pkg in _pkgs:
        _bin = ROOT / "ros2_ws" / _overlay / _pkg / "bin"
        if _bin.is_dir():
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(_bin))
            if str(_bin) not in os.environ.get("PATH", ""):
                os.environ["PATH"] = str(_bin) + os.pathsep + os.environ.get("PATH", "")
        _site = ROOT / "ros2_ws" / _overlay / _pkg / "Lib" / "site-packages"
        if _site.is_dir() and str(_site) not in sys.path:
            sys.path.insert(0, str(_site))

from sp_msgs.msg import AutoAimCommandMsg  # noqa: E402


def _make_image(path: Path) -> Image:
    bgr = cv2.imread(str(path))
    assert bgr is not None, f"Failed to read {path}"
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    msg = Image()
    msg.height, msg.width = rgb.shape[:2]
    msg.encoding = "rgb8"
    msg.step = msg.width * 3
    msg.data = rgb.tobytes()
    return msg


def main() -> int:
    cpp_exe = ROOT / "ros2_ws/install_vision/sp_vision_25/lib/sp_vision_25/vision_node.exe"
    assets_dir = ROOT / "ros2_ws/src/sp_vision_25/assets"
    assert cpp_exe.is_file(), f"Compiled C++ vision_node not found: {cpp_exe}"

    os.environ["ROS_DOMAIN_ID"] = "79"
    proc_env = dict(os.environ)
    proc_env["ROS_DOMAIN_ID"] = "79"

    proc = subprocess.Popen(
        [
            str(cpp_exe),
            "--ros-args",
            "-p",
            "robot_id:=aim_sentry",
            "-p",
            f"assets_dir:={assets_dir}",
            "-p",
            "require_behavior_switch:=true",
            "-p",
            "debug_image:=true",
        ],
        env=proc_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        rclpy.init()
        received_cmds: list[AutoAimCommandMsg] = []
        received_permits: list[int] = []
        received_tracks: list[dict] = []
        received_status: list[dict] = []
        received_debug: list[tuple[int, int]] = []

        tester = Node("sp_vision_cpp_loopback_tester")
        rel_qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=5, reliability=ReliabilityPolicy.RELIABLE)
        img_pub = tester.create_publisher(Image, "/aim_sentry/camera/image_raw", qos_profile_sensor_data)
        ref_pub = tester.create_publisher(String, "/rmsim/referee/state", rel_qos)
        amcl_pub = tester.create_publisher(PoseWithCovarianceStamped, "/amcl_pose", rel_qos)
        bt_switch_pub = tester.create_publisher(Int32, "/aim_sentry/behavior/auto_aim_switch", 5)
        bt_fire_pub = tester.create_publisher(Int32, "/aim_sentry/behavior/fire_permit", 5)

        tester.create_subscription(AutoAimCommandMsg, "/aim_sentry/autoaim_command", received_cmds.append, 10)
        tester.create_subscription(Int32, "/aim_sentry/auto_aim_switch", lambda m: received_permits.append(m.data), 10)
        tester.create_subscription(String, "/aim_sentry/behavior/vision_track", lambda m: received_tracks.append(json.loads(m.data)), 10)
        tester.create_subscription(String, "/sp_vision/status", lambda m: received_status.append(json.loads(m.data)), rel_qos)
        tester.create_subscription(Image, "/sp_vision/debug_image", lambda m: received_debug.append((m.width, m.height)), qos_profile_sensor_data)

        exe = SingleThreadedExecutor()
        exe.add_node(tester)

        # Wait for DDS discovery of C++ vision_node
        t_disc = time.monotonic()
        while time.monotonic() - t_disc < 8.0:
            exe.spin_once(timeout_sec=0.05)
            if len(received_tracks) > 0:
                break
        assert len(received_tracks) > 0, "Did not discover C++ vision_node on /aim_sentry/behavior/vision_track"

        # Publish match-in-progress referee + AMCL pose
        ref_payload = json.dumps({
            "game_status": {"game_progress": 4},
            "robot_status": {"robot_id": 7, "current_hp": 600},
            "all_robot_hp": {"blue_7_robot_hp": 600},
        })
        pose_msg = PoseWithCovarianceStamped()
        pose_msg.pose.pose.position.x = 2.96
        pose_msg.pose.pose.position.y = 0.0
        pose_msg.pose.pose.position.z = 0.30
        for _ in range(5):
            ref_pub.publish(String(data=ref_payload))
            amcl_pub.publish(pose_msg)
            exe.spin_once(timeout_sec=0.03)

        frames_dir = ROOT / "artifacts/unreal/armor-camera-20261125-neutral"
        frame0 = _make_image(frames_dir / "sentry-blue-0.png")
        frame1 = _make_image(frames_dir / "sentry-blue-1.png")
        frame2 = _make_image(frames_dir / "sentry-blue-2.png")

        # Phase 1: behavior switch = 0 -> must NOT publish commands
        bt_switch_pub.publish(Int32(data=0))
        for _ in range(5):
            exe.spin_once(timeout_sec=0.02)
        img_pub.publish(frame0)
        for _ in range(15):
            exe.spin_once(timeout_sec=0.02)
        assert len(received_cmds) == 0, f"Phase 1 failed: got {len(received_cmds)} cmds with switch=0"

        # Phase 2: behavior switch = 1, fire_permit = 0 -> locks (control=True), must NOT shoot
        for img in (frame0, frame1):
            prev = len(received_cmds)
            for tick in range(40):
                bt_switch_pub.publish(Int32(data=1))
                bt_fire_pub.publish(Int32(data=0))
                if tick % 5 == 0:
                    img_pub.publish(img)
                exe.spin_once(timeout_sec=0.03)
                if len(received_cmds) > prev:
                    break
        assert len(received_cmds) >= 2, f"Phase 2 failed: expected >=2 cmds, got {len(received_cmds)}"
        assert all(m.control and not m.shoot for m in received_cmds), "Phase 2 failed: shot while fire_permit=0"

        # Phase 3: behavior switch = 1, fire_permit = 1 -> locks AND shoots
        prev = len(received_cmds)
        for tick in range(40):
            bt_switch_pub.publish(Int32(data=1))
            bt_fire_pub.publish(Int32(data=1))
            if tick % 5 == 0:
                img_pub.publish(frame2)
            exe.spin_once(timeout_sec=0.03)
            if any(m.control and m.shoot for m in received_cmds[prev:]):
                break
        assert any(m.control and m.shoot for m in received_cmds[prev:]), f"Phase 3 failed: expected shoot=True, got {received_cmds[prev:]}"
        for _ in range(8):
            bt_switch_pub.publish(Int32(data=1))
            bt_fire_pub.publish(Int32(data=1))
            exe.spin_once(timeout_sec=0.03)

        # Phase 4: behavior switch = 0 -> releases permit (1 -> 0)
        bt_switch_pub.publish(Int32(data=0))
        for _ in range(15):
            exe.spin_once(timeout_sec=0.02)
        assert 1 in received_permits and received_permits[-1] == 0, f"Phase 4 failed: permits={received_permits}"
        assert any(t.get("label") == 5 and t.get("enemy_x") is not None for t in received_tracks), "Missing sentry label=5 + AMCL enemy_x"
        assert len(received_status) >= 3, f"Expected >=3 /sp_vision/status msgs, got {len(received_status)}"
        assert len(received_debug) >= 3 and received_debug[-1] == (1280, 720), f"Debug image check failed: {received_debug}"

        print(
            f"CPP_ROS2_LOOPBACK_OK cmds={len(received_cmds)} permits={received_permits} "
            f"tracks={len(received_tracks)} status={len(received_status)} debug_imgs={len(received_debug)}",
            flush=True,
        )
        for idx, m in enumerate(received_cmds):
            print(
                f"  cmd[{idx}]: control={m.control} shoot={m.shoot} yaw={m.yaw:.4f} pitch={m.pitch:.4f}",
                flush=True,
            )
        print(f"  last_status: {received_status[-1]}", flush=True)

        tester.destroy_node()
        rclpy.shutdown()
        return 0
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=3.0)
            print("=== C++ vision_node output ===")
            print(out.strip(), flush=True)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
