"""Replay UE camera crops through both implementations of the simulator vision node."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import CameraInfo, Image
from std_msgs.msg import String

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[2]
NEUTRAL = ROOT / "artifacts/unreal/armor-camera-20261125-neutral"
INFANTRY = ROOT / "artifacts/unreal/rmuc-infantry-appearance-20261126"


def scene(*plates: tuple[Path, int]) -> np.ndarray:
    canvas = np.full((720, 1280, 3), 32, dtype=np.uint8)
    for path, x in plates:
        source = cv2.imread(str(path))
        assert source is not None, path
        canvas[230:490, x:x + 310] = source[230:490, 485:795]
    return cv2.resize(canvas, (640, 360), interpolation=cv2.INTER_AREA)


def run(backend: str, domain: int) -> list[str]:
    os.environ["ROS_DOMAIN_ID"] = str(domain)
    child_env = dict(os.environ)
    if backend == "native":
        overlay_bins = [
            ROOT / "ros2_ws/install_control/sp_msgs/bin",
            ROOT / "ros2_ws/install_behavior/dji_referee_protocol/bin",
        ]
        child_env["PATH"] = os.pathsep.join([*(str(p) for p in overlay_bins), child_env["PATH"]])
        binary = ROOT / "ros2_ws/install_vision/sp_vision_25/lib/sp_vision_25/vision_node.exe"
        command = [str(binary)]
    else:
        child_env["PYTHONPATH"] = os.pathsep.join([str(PACKAGE), child_env.get("PYTHONPATH", "")])
        command = [sys.executable, "-m", "sp_vision_25.vision_node"]
    command.extend([
        "--ros-args", "-p", f"assets_dir:={PACKAGE / 'assets'}",
        "-p", "referee_mode:=sim", "-p", "use_sim_time:=true",
    ])
    process = subprocess.Popen(command, env=child_env)

    rclpy.init()
    node = Node("rmsim_vision_replay")
    exe = SingleThreadedExecutor()
    exe.add_node(node)
    clock_pub = node.create_publisher(Clock, "/clock", qos_profile_sensor_data)
    image_pub = node.create_publisher(Image, "/aim_sentry/camera/image_raw", qos_profile_sensor_data)
    info_pub = node.create_publisher(CameraInfo, "/aim_sentry/camera/camera_info", qos_profile_sensor_data)
    referee_pub = node.create_publisher(String, "/rmsim/referee/state", 5)
    priority_pub = node.create_publisher(String, "/aim_sentry/behavior/target_priority", 5)
    track: dict = {}
    checks: list[str] = []
    simulated_ns = 1_000_000_000

    def observe(msg: String) -> None:
        track.update(json.loads(msg.data))

    def stamp_ns(msg: Image) -> int:
        stamp = msg.header.stamp
        return stamp.sec * 10**9 + stamp.nanosec

    node.create_subscription(String, "/aim_sentry/behavior/vision_track", observe, 10)

    def spin_until(condition, timeout=3.0) -> None:
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            exe.spin_once(timeout_sec=0.012)
        assert condition(), {"track": track, "process": process.poll()}

    def settle(duration=0.10) -> None:
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            exe.spin_once(timeout_sec=0.010)

    def advance(duration_ns: int, *, wait_for_track: bool = True) -> None:
        nonlocal simulated_ns
        simulated_ns += duration_ns
        msg = Clock()
        msg.clock.sec, msg.clock.nanosec = divmod(simulated_ns, 10**9)
        for _ in range(3):
            clock_pub.publish(msg)
            settle(0.025)
        if wait_for_track:
            spin_until(lambda: track.get("stamp_ns") == simulated_ns)

    def frame(image: np.ndarray) -> Image:
        msg = Image()
        msg.header.stamp.sec, msg.header.stamp.nanosec = divmod(simulated_ns, 10**9)
        msg.height, msg.width = image.shape[:2]
        msg.encoding = "bgr8"
        msg.step = msg.width * 3
        msg.data = image.tobytes()
        return msg

    def send(image: np.ndarray) -> Image:
        advance(20_000_000, wait_for_track=False)
        msg = frame(image)
        for _ in range(3):
            image_pub.publish(msg)
            settle(0.065)
        advance(60_000_000)
        return msg

    def expect(msg: Image, label: int, track_id: int | None = None) -> int:
        assert track.get("observation_stamp_ns") == stamp_ns(msg) and track["visible"], track
        assert track["label"] == label, track
        if track_id is not None:
            assert track["track_id"] == track_id, track
        return track["track_id"]

    def referee(friendly_red: bool, same_team_outpost: bool = False) -> None:
        payload = {
            "available": True,
            # Deliberately disagree with the legacy robot id to prove that the local side wins.
            "friendly_red": friendly_red,
            "robot_status": {"robot_id": 107 if friendly_red else 7, "current_hp": 600},
            "game_status": {"game_progress": 4},
            "all_robot_hp": {"red_7_robot_hp": 600, "blue_7_robot_hp": 600, "blue_4_robot_hp": 0},
            "field_devices": {
                "red": {"outpost_hp": 1000, "outpost_owner_red": True},
                "blue": {"outpost_hp": 1000, "outpost_owner_red": same_team_outpost},
            },
        }
        for _ in range(3):
            referee_pub.publish(String(data=json.dumps(payload)))
            settle(0.04)
        advance(60_000_000)

    red = scene((NEUTRAL / "sentry-red-0.png", 340))
    blue_outpost = NEUTRAL / "outpost-blue-0.png"
    blue_sentry = NEUTRAL / "sentry-blue-0.png"
    outpost_alone = scene((blue_outpost, 340))
    both = scene((blue_outpost, 360), (blue_sentry, 790))
    sentry_alone = scene((blue_sentry, 790))

    try:
        spin_until(
            lambda: all(
                pub.get_subscription_count() > 0 for pub in (clock_pub, referee_pub, image_pub, priority_pub, info_pub)
            ),
            timeout=8.0,
        )
        advance(0)
        info = CameraInfo()
        info.width, info.height = 640, 360
        info.k = [554.25, 0.0, 320.0, 0.0, 554.25, 180.0, 0.0, 0.0, 1.0]
        info_pub.publish(info)
        send(sentry_alone)
        assert track["track_id"] is None, track
        checks.append("no acquisition before the simulator publishes its team")

        referee(friendly_red=True)
        priority_pub.publish(String(data="ENEMY_VEHICLE_THEN_OUTPOST"))
        settle()
        send(red)
        assert track["track_id"] is None, track
        one_colored = outpost_alone.copy()
        one_colored[:, :225] = cv2.cvtColor(
            cv2.cvtColor(one_colored[:, :225], cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR
        )
        send(one_colored)
        assert track["track_id"] is None, track
        checks.append("friendly armor and mixed-color lamp pair produce no observation")

        outpost_id = expect(send(outpost_alone), 6)
        for _ in range(3):
            expect(send(both), 6, outpost_id)
        checks.append("blue outpost retains one track id through plate motion and simultaneous blue sentry")

        earlier = track["observation_stamp_ns"]
        send(sentry_alone)
        spin_until(lambda: not track["visible"])
        assert track["track_id"] == outpost_id and track["observation_stamp_ns"] == earlier, track

        advance(260_000_000)
        missing = send(sentry_alone)
        for _ in range(3):
            image_pub.publish(missing)
            settle(0.075)
        advance(60_000_000)
        assert track["track_id"] == outpost_id and track["observation_stamp_ns"] == earlier, track
        send(sentry_alone)
        assert track["track_id"] == outpost_id, track
        sentry_id = expect(send(sentry_alone), 5)
        assert sentry_id != outpost_id, track
        checks.append("one occluded frame and repeated capture stamps keep old id until sustained handoff")

        referee(friendly_red=False)
        spin_until(lambda: track["track_id"] is None)
        send(sentry_alone)
        assert track["track_id"] is None, track
        expect(send(red), 5)
        checks.append("side swap releases the old observation and reverses friendly filtering")

        referee(friendly_red=True, same_team_outpost=True)
        spin_until(lambda: track["track_id"] is None)
        send(outpost_alone)
        assert track["track_id"] is None, track
        infantry = INFANTRY / "infantry-red3-blue4/captures/sentry-blue-0.png"
        expect(send(scene((infantry, 340))), 3)
        checks.append("captured friendly outpost excluded, blue four with zero roster HP still recognized")

        return checks
    finally:
        exe.shutdown()
        node.destroy_node()
        rclpy.shutdown()
        process.terminate()
        process.wait(timeout=4)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("backend", choices=("native", "python"))
    parser.add_argument("--domain", type=int, default=91)
    args = parser.parse_args()
    print(json.dumps({"backend": args.backend, "checks": run(args.backend, args.domain)}, indent=2))

