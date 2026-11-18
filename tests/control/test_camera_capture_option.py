"""RGB opt-out boundary and wire compatibility; no UE or ROS processes started."""
import contextlib
import copy
import io
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/integration"))
sys.path.insert(0, str(ROOT / "ros2_ws/src/rmsim_bridge"))
import navigation_console
from rmsim_bridge import protocol


class ValidatedSceneRequest(Exception):
    pass


def state_fixture():
    pose = {"position_m": [0., 0., 0.], "rotation_xyzw": [0., 0., 0., 1.]}
    return {
        "v": 1, "kind": "state", "session": "camera-option-test",
        "robot_id": "aim_sentry", "epoch": 0, "tick": 1, "sim_time_ns": 1_000_000_000,
        "pose": copy.deepcopy(pose),
        "camera": {
            "width": 320, "height": 240, "hfov_rad": math.pi/3,
            "frame_id": "aim_sentry/aim_camera_optical_frame",
            "stamp_ns": 1_000_000_000, "pose_in_base": copy.deepcopy(pose),
            "nominal_hz": 0., "capture_enabled": False,
        },
        "command": {"status": "waiting", "accepted_seq": -1, "age_s": -1.},
    }


class CameraCaptureOptionTests(unittest.TestCase):
    def console_validation(self, arguments, expected):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(navigation_console, "ROOT", root), \
                 patch.object(navigation_console, "prepare_scene", side_effect=ValidatedSceneRequest), \
                 patch.object(sys, "argv", ["navigation_console.py", *arguments]), \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(expected) as result:
                    navigation_console.main()
                if expected is SystemExit:
                    self.assertEqual(result.exception.code, 2)
                    self.assertFalse((root / "artifacts").exists())

    def test_navigation_default_reaches_scene_preparation(self):
        self.console_validation([], ValidatedSceneRequest)

    def test_explicit_disabled_and_enabled_rates_are_accepted(self):
        for hz in ["0", "1", "10", "59.94", "60"]:
            with self.subTest(hz=hz):
                self.console_validation(["--camera-hz", hz], ValidatedSceneRequest)

    def test_invalid_rates_fail_before_starting_a_session(self):
        for hz in ["-1", "0.1", "0.999", "60.001", "nan", "inf"]:
            with self.subTest(hz=hz):
                self.console_validation(["--camera-hz", hz], SystemExit)

    def test_zero_rgb_preserves_calibration_and_transform(self):
        state = state_fixture()
        before = copy.deepcopy(state["camera"])
        decoded = protocol.validate_state(state, b"", "aim_sentry")
        self.assertEqual(decoded["camera"], before)
        self.assertEqual(len(protocol.intrinsics(320, 240, before["hfov_rad"])), 9)
        self.assertEqual(decoded["camera"]["frame_id"], "aim_sentry/aim_camera_optical_frame")

    def test_lidar_binary_starts_at_zero_without_rgb(self):
        state = state_fixture()
        state.update(v=2, camera_bytes=0)
        state["lidar"] = {
            "calibrated": False, "id": "lidar", "frame_id": "aim_sentry/lidar_link",
            "parent_frame_id": "aim_sentry/base_link",
            "pose_in_base": copy.deepcopy(state["pose"]),
            "model": "ideal", "profile_md5": "test",
            "rays_per_second": 200000, "frame_hz": 10,
            "min_range_m": .1, "max_range_m": 40.,
            "elevation_min_rad": -.1, "elevation_max_rad": 1.,
            "traced_rays": 1, "returns": 1, "missed_rays": 0,
            "dropped_scans": 0, "history_resets": 0, "max_physics_step_s": .005,
            "scans": [{"seq": 1, "start_ns": 900_000_000, "end_ns": 1_000_000_000,
                       "point_count": 1, "byte_length": 20, "byte_offset": 0}],
        }
        payload = protocol.LIDAR_POINT.pack(99_999_999, 1., 2., .1, 128, 0, 1, 0)
        self.assertIs(protocol.validate_state(state, payload, "aim_sentry"), state)
        encoded = json.dumps(state).encode("utf-8")
        packet = struct.pack("<II", len(encoded), len(payload)) + encoded + payload
        reader = protocol.PacketReader()
        packets = []
        for start in range(0, len(packet), 31):
            packets.extend(reader.feed(packet[start:start+31]))
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0][1], payload)
        self.assertEqual(packets[0][0]["camera_bytes"], 0)
        protocol.validate_state(*packets[0], "aim_sentry")

    def test_enabled_rgb_retains_existing_wire_contract(self):
        state = state_fixture()
        state["camera"].update(nominal_hz=10., capture_enabled=True)
        pixels = bytes([12, 34, 56]) * (320*240)
        self.assertIs(protocol.validate_state(state, pixels, "aim_sentry"), state)

    def test_malformed_rgb_size_is_still_rejected(self):
        with self.assertRaisesRegex(protocol.ProtocolError, "RGB8"):
            protocol.validate_state(state_fixture(), b"invalid", "aim_sentry")


if __name__ == "__main__":
    unittest.main()
