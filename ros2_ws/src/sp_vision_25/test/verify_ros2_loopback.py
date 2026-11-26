"""End-to-end ROS 2 DDS loopback verification for sp_vision_25 + behavior switch."""
from __future__ import annotations

import json
from pathlib import Path
import cv2
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import qos_profile_sensor_data
from geometry_msgs.msg import PoseWithCovarianceStamped
from sensor_msgs.msg import Image
from std_msgs.msg import Int32, String

from sp_vision_25.vision_node import HAS_SP_MSGS, SpVisionSimNode
from sp_msgs.msg import AutoAimCommandMsg


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
    rclpy.init()
    vision = SpVisionSimNode()
    vision.set_parameters([Parameter("require_behavior_switch", Parameter.Type.BOOL, True)])
    vision.require_behavior_switch = True
    vision.behavior_permits = False
    vision.game_running = True
    assert HAS_SP_MSGS and vision.cmd_pub is not None, "sp_msgs publisher failed"

    received_cmds: list[AutoAimCommandMsg] = []
    received_permits: list[int] = []
    received_tracks: list[dict] = []

    tester = Node("sp_vision_loopback_tester")
    img_pub = tester.create_publisher(Image, "/aim_sentry/camera/image_raw", qos_profile_sensor_data)
    amcl_pub = tester.create_publisher(PoseWithCovarianceStamped, "/amcl_pose", 5)
    bt_switch_pub = tester.create_publisher(Int32, "/aim_sentry/behavior/auto_aim_switch", 5)
    bt_fire_pub = tester.create_publisher(Int32, "/aim_sentry/behavior/fire_permit", 5)
    tester.create_subscription(AutoAimCommandMsg, "/aim_sentry/autoaim_command", received_cmds.append, 10)
    tester.create_subscription(Int32, "/aim_sentry/auto_aim_switch", lambda m: received_permits.append(m.data), 10)
    tester.create_subscription(String, "/aim_sentry/behavior/vision_track", lambda m: received_tracks.append(json.loads(m.data)), 10)

    exe = SingleThreadedExecutor()
    exe.add_node(vision)
    exe.add_node(tester)
    for _ in range(10):
        exe.spin_once(timeout_sec=0.03)

    pose_msg = PoseWithCovarianceStamped()
    pose_msg.pose.pose.position.x = 2.96
    pose_msg.pose.pose.position.y = 0.0
    pose_msg.pose.pose.position.z = 0.30
    amcl_pub.publish(pose_msg)
    for _ in range(5):
        exe.spin_once(timeout_sec=0.02)

    frames_dir = Path(r"D:\simulator\artifacts\unreal\armor-camera-20261125-neutral")
    frame0 = _make_image(frames_dir / "sentry-blue-0.png")
    frame1 = _make_image(frames_dir / "sentry-blue-1.png")
    frame2 = _make_image(frames_dir / "sentry-blue-2.png")

    # Phase 1: behavior auto_aim_switch = 0 -> must NOT publish any command
    bt_switch_pub.publish(Int32(data=0))
    for _ in range(5):
        exe.spin_once(timeout_sec=0.02)
    img_pub.publish(frame0)
    for _ in range(10):
        exe.spin_once(timeout_sec=0.02)
    assert len(received_cmds) == 0, f"Expected 0 cmds while behavior switch=0, got {len(received_cmds)}"

    # Phase 2: behavior auto_aim_switch = 1, fire_permit = 0 -> locks (control=True), must NOT shoot
    bt_switch_pub.publish(Int32(data=1))
    bt_fire_pub.publish(Int32(data=0))
    for _ in range(5):
        exe.spin_once(timeout_sec=0.02)
    for img in (frame0, frame1):
        prev = len(received_cmds)
        bt_switch_pub.publish(Int32(data=1))
        bt_fire_pub.publish(Int32(data=0))
        img_pub.publish(img)
        for _ in range(30):
            exe.spin_once(timeout_sec=0.02)
            if len(received_cmds) > prev:
                break
    assert len(received_cmds) >= 2, f"Expected >=2 cmds in phase 2, got {len(received_cmds)}"
    assert all(m.control and not m.shoot for m in received_cmds), "Must not shoot when fire_permit=0"

    # Phase 3: behavior auto_aim_switch = 1, fire_permit = 1 -> locks AND shoots
    prev = len(received_cmds)
    bt_switch_pub.publish(Int32(data=1))
    bt_fire_pub.publish(Int32(data=1))
    for _ in range(15):
        exe.spin_once(timeout_sec=0.01)
        if vision.fire_permitted is True:
            break
    assert vision.fire_permitted is True, "fire_permit=1 was not received"
    bt_switch_pub.publish(Int32(data=1))
    bt_fire_pub.publish(Int32(data=1))
    img_pub.publish(frame2)
    for _ in range(30):
        exe.spin_once(timeout_sec=0.02)
        if any(m.control and m.shoot for m in received_cmds[prev:]):
            break
    assert any(m.control and m.shoot for m in received_cmds[prev:]), f"Expected shoot=True in phase 3, got {received_cmds[prev:]}"
    for _ in range(5):
        exe.spin_once(timeout_sec=0.02)

    # Phase 4: behavior auto_aim_switch = 0 -> must release permit (publish 0 to /aim_sentry/auto_aim_switch)
    bt_switch_pub.publish(Int32(data=0))
    for _ in range(10):
        exe.spin_once(timeout_sec=0.02)
    assert 1 in received_permits and received_permits[-1] == 0, f"Expected permit 1->0 release, got {received_permits}"
    assert any(t.get("label") == 5 and t.get("enemy_x") is not None for t in received_tracks), "Expected sentry label=5 + AMCL enemy_x in behavior/vision_track"

    print(
        f"ROS2_BT_INTEGRATION_OK sp_msgs={HAS_SP_MSGS} cmds={len(received_cmds)} "
        f"permits={received_permits} tracks={len(received_tracks)}",
        flush=True,
    )
    for idx, m in enumerate(received_cmds):
        print(
            f"  cmd[{idx}]: control={m.control} shoot={m.shoot} "
            f"yaw={m.yaw:.4f} pitch={m.pitch:.4f}",
            flush=True,
        )

    vision.destroy_node()
    tester.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
