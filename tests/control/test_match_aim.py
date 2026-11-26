"""Regression coverage for simulator-only match aim ownership and visual lock leases."""
import json
import math
from collections import deque
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "ros2_ws/src" / package) for package in
                ("rmsim_bridge", "rmsim_sentry_behavior")]
try:
    from rmsim_sentry_behavior import vision_autoaim_node as vision
    from rmsim_bridge.aim_adapter import AimAdapter, AutoAimCommandMsg, GimbalCmd
    from geometry_msgs.msg import TwistStamped
    from nav_msgs.msg import Odometry
    from sensor_msgs.msg import Image
    from std_msgs.msg import Int32, String
except ModuleNotFoundError as error:
    if error.name not in {"rclpy", "sp_msgs", "pb_rm_interfaces", "numpy", "cv2"}:
        raise
    raise unittest.SkipTest("requires the simulator ROS 2 and OpenCV environment") from error


class MatchAimTests(unittest.TestCase):
    def test_moving_follow_vision_hold_and_stationary_scan(self):
        packets = []

        def queue(raw):
            packets.append(json.loads(raw))
            return True

        state = {
            "robot_id": "aim_sentry", "session": "match", "epoch": 0, "sim_time_ns": 0,
            "drive": {"match_autonomy": True},
            "aim": {
                "enabled": True, "match_fire": {}, "world_pitch_rad": 0.,
                "yaw_rad": 0., "pitch_rad": 0., "yaw_rate_limit": 9.,
                "pitch_rate_limit": 6., "pitch_min_rad": -.7, "pitch_max_rad": .7,
            },
        }
        params = {
            "navigation_follow": True,
            "navigation_follow_profile": str(ROOT / "assets/vehicles/aim_sentry/navigation-follow.json"),
        }
        node = SimpleNamespace(
            robot_id=state["robot_id"], state=state, sequence=0, forwarded=0, received_at=0.,
            transport=SimpleNamespace(connected=True, queue=queue),
            declare_parameter=lambda name, default: SimpleNamespace(value=params.get(name, default)),
            create_subscription=Mock(),
        )
        adapter = AimAdapter(node)
        adapter.observe(state)

        def tick(milliseconds, travel_speed, aim=None, request=1.):
            odom = Odometry()
            odom.header.frame_id = "aim_sentry/odom"
            odom.child_frame_id = "aim_sentry/base_link"
            odom.header.stamp.sec, odom.header.stamp.nanosec = divmod(milliseconds * 1_000_000, 1_000_000_000)
            odom.pose.pose.orientation.w = 1.
            odom.twist.twist.linear.y = travel_speed
            velocity = TwistStamped()
            velocity.header.stamp = odom.header.stamp
            velocity.header.frame_id = odom.child_frame_id
            velocity.twist.linear.x = request
            scan = GimbalCmd()
            scan.header.stamp = odom.header.stamp
            scan.yaw_type = scan.pitch_type = 1
            state["sim_time_ns"] = milliseconds * 1_000_000
            node.received_at = milliseconds / 1000
            with patch("rmsim_bridge.aim_adapter.time.monotonic", return_value=node.received_at):
                adapter.navigation.on_odometry(odom)
                adapter.navigation.observe_velocity(velocity)
                adapter.on_scan(scan)
                if aim is not None:
                    adapter.on_switch(Int32(data=1))
                    adapter.on_auto(aim)
                adapter.poll()
            return packets[-1]

        tick(1000, .7)
        tick(1010, .7)
        self.assertEqual(adapter.source, "navigation")
        self.assertAlmostEqual(adapter.navigation.report["destination_in_odom"], math.pi / 2)
        command = AutoAimCommandMsg(control=True, shoot=True, yaw=-1., pitch=.1)
        command.timestamp.sec, command.timestamp.nanosec = 1, 20_000_000
        shot = tick(1020, .7, command)
        self.assertEqual((adapter.source, shot["fire"]), ("auto", 1))
        held = tick(1240, .7)
        self.assertEqual((adapter.source, held["fire"], held["permit"]), ("auto", 0, 0))
        self.assertAlmostEqual(held["yaw"], shot["yaw"])
        self.assertEqual(adapter.auto["stamp"], 1_020_000_000)
        expired = tick(1380, .7)
        self.assertEqual((adapter.source, expired["fire"]), ("navigation", 0))
        tick(1390, 0.)
        self.assertEqual(adapter.source, "scan")
        tick(1400, .2, request=0.)
        self.assertEqual(adapter.source, "scan")
        state["drive"]["match_autonomy"] = False
        tick(1410, .7)
        self.assertEqual(adapter.source, "scan")

    def test_bar_pair_requires_one_slender_component(self):
        classifier = Mock()
        classifier.forward.return_value = vision.np.eye(1, 8, vision.SENTRY) * 10.
        frame = vision.np.zeros((36, 64, 3), dtype=vision.np.uint8)
        frame[14:20, 20:24] = (255, 0, 0)
        frame[14:20, 36:40] = (255, 0, 0)
        self.assertEqual(vision.detect_enemy_armors(frame, False, classifier), [])
        classifier.forward.assert_not_called()

        frame[14:20, 22:24] = 0
        self.assertEqual([det.label for det in vision.detect_enemy_armors(frame, False, classifier)], [vision.SENTRY])

    def test_outpost_targetability_follows_capture_and_owner_swap(self):
        target = vision.VisionAutoAimNode.__new__(vision.VisionAutoAimNode)
        target.is_red = True
        target.target_priority = "ENEMY_VEHICLE_THEN_OUTPOST"
        home = {"outpost_hp": 350, "outpost_owner_red": False}
        far = {"outpost_hp": 350, "outpost_owner_red": True, "base_hp": 5000, "base_protected": True}
        referee = {
            "game_status": {"game_progress": 4},
            "robot_status": {"robot_id": 7, "current_hp": 400},
            "all_robot_hp": {"blue_7_robot_hp": 300},
            "field_devices": {"red": home, "blue": far},
        }

        def allowed():
            target._on_referee(String(data=json.dumps(referee)))
            return target._allowed_labels()

        self.assertEqual(allowed(), {vision.SENTRY, vision.OUTPOST})
        home["outpost_hp"] = 0
        self.assertEqual(allowed(), {vision.SENTRY})
        home.update(outpost_hp=350, outpost_owner_red=True)
        self.assertEqual(allowed(), {vision.SENTRY})
        far["outpost_owner_red"] = False
        self.assertEqual(allowed(), {vision.SENTRY, vision.OUTPOST})

    def test_priority_and_short_feedback_gap_keep_the_same_visual_lock(self):
        target = vision.VisionAutoAimNode.__new__(vision.VisionAutoAimNode)
        current_stamp = 0
        target.get_clock = lambda: SimpleNamespace(now=lambda: SimpleNamespace(nanoseconds=current_stamp))
        target.get_logger = Mock()
        target.track_pub, target.permit_pub, target.cmd_pub = Mock(), Mock(), Mock()
        target.identity = ("match", 0)
        target.is_red, target.game_running, target.robot_alive = True, True, True
        target.sim_match, target.behavior_permits, target.behavior_stamp = True, True, 1_000_000_000
        target.fire_permitted = target.fire_permit_stamp = None
        target.target_priority, target.targets = "ENEMY_VEHICLE_FIRST", {vision.SENTRY}
        target.track, target.track_id, target.track_stamp, target.track_hits = None, 0, 0, 0
        target.track_visible = target.command_firing = target.permitted = False
        target.intrinsics = (2, 2, "camera", 12., 12., 1., 1.)
        target.feedback, target.pending_image = deque(maxlen=32), None
        target.frames = target.paired = target.tracked = target.trigger_frames = target.last_diagnostic = 0
        target.outpost_reference = target.classifier = None

        referee = {
            "game_status": {"game_progress": 4},
            "robot_status": {"robot_id": 7, "current_hp": 400},
            "all_robot_hp": {"blue_7_robot_hp": 300},
            "field_devices": {"blue": {
                "outpost_hp": 1000, "outpost_owner_red": False, "base_hp": 5000, "base_protected": True,
            }},
        }
        target._on_priority(String(data="ENEMY_VEHICLE_THEN_BASE"))
        target._on_referee(String(data=json.dumps(referee)))
        self.assertEqual(target._allowed_labels(), {vision.SENTRY})
        referee["field_devices"]["blue"]["base_protected"] = False
        target._on_referee(String(data=json.dumps(referee)))
        self.assertEqual(target._allowed_labels(), {vision.SENTRY, vision.BASE})
        target._on_priority(String(data="ENEMY_VEHICLE_THEN_OUTPOST"))
        self.assertEqual(target._allowed_labels(), {vision.SENTRY, vision.OUTPOST})

        def image(milliseconds, detections, paired=True):
            nonlocal current_stamp
            current_stamp = milliseconds * 1_000_000
            msg = Image(width=2, height=2, step=6, encoding="rgb8", data=[0] * 12)
            msg.header.frame_id = "camera"
            msg.header.stamp.sec, msg.header.stamp.nanosec = divmod(current_stamp, 1_000_000_000)
            if paired:
                target.feedback.append((current_stamp, 0., 0.))
            with patch.object(vision, "detect_enemy_armors", return_value=detections):
                target._on_image(msg)

        sentry = vision.ArmorObservation(1., 1., 1., vision.SENTRY, .80)
        outpost = vision.ArmorObservation(1., 1., 1., vision.OUTPOST, .95)
        image(1000, [outpost])
        self.assertEqual(target.track.label, vision.OUTPOST)
        image(1040, [outpost, sentry])
        self.assertEqual(target.track.label, vision.SENTRY)
        self.assertEqual(target.track_id, 2)
        image(1080, [sentry])
        self.assertTrue(target.cmd_pub.publish.call_args.args[0].shoot)
        current_stamp = 1090_000_000
        target._on_fire_permit(Int32(data=0))
        command = target.cmd_pub.publish.call_args.args[0]
        self.assertEqual((target.track_id, target.track_visible, command.control, command.shoot),
                         (2, True, True, False))
        image(1100, [], paired=False)
        current_stamp = 1300_000_000
        target.behavior_stamp = current_stamp
        target._watchdog()
        lock = json.loads(target.track_pub.publish.call_args.args[0].data)
        self.assertEqual((lock["track_id"], lock["visible"], lock["firing"]), (2, False, False))
        image(1320, [sentry])
        self.assertEqual(target.track_id, 2)
        self.assertFalse(target.cmd_pub.publish.call_args.args[0].shoot)
        target._on_priority(String(data="ENEMY_VEHICLE_FIRST"))
        self.assertEqual(target.track_id, 2)
        current_stamp = 1330_000_000
        target._on_fire_permit(Int32(data=1))
        image(1590, [sentry])
        self.assertTrue(target.cmd_pub.publish.call_args.args[0].shoot)
        current_stamp = 1640_000_000
        target.behavior_stamp = current_stamp
        target._watchdog()
        command = target.cmd_pub.publish.call_args.args[0]
        lock = json.loads(target.track_pub.publish.call_args.args[0].data)
        self.assertEqual((lock["track_id"], lock["visible"], lock["fire_permit"], command.control, command.shoot),
                         (2, True, False, True, False))
        current_stamp = 1950_000_000
        target.behavior_stamp = current_stamp
        target._watchdog()
        self.assertIsNone(target.track)
        self.assertEqual(target.permit_pub.publish.call_args.args[0].data, 0)


if __name__ == "__main__":
    unittest.main()
