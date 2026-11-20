"""Independent datum, external-calibration and profile checks for source-derived MID-360 mounts."""
import copy
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "assets/vehicles/aim_sentry/sensors"
sys.path.insert(0, str(ROOT / "ros2_ws/src/rmsim_bridge"))


def read(name):
    return json.loads((PROFILES / name).read_text(encoding="utf-8"))


def matrix(rpy):
    r, p, y = rpy
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return [[cy*cp, cy*sp*sr-sy*cr, cy*sp*cr+sy*sr],
            [sy*cp, sy*sp*sr+cy*cr, sy*sp*cr-cy*sr],
            [-sp, cp*sr, cp*cr]]


def quaternion(rpy):
    r, p, y = (v / 2 for v in rpy)
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return [sr*cp*cy-cr*sp*sy, cr*sp*cy+sr*cp*sy, cr*cp*sy-sr*sp*cy, cr*cp*cy+sr*sp*sy]


def runtime_mount(profile):
    return dict(id=profile["id"], parent_frame_id="aim_sentry/" + profile["parent_frame"],
                mount_schema=2, pose_in_parent=dict(position_m=profile["origin"]["xyz"],
                rotation_xyzw=quaternion(profile["origin"]["rpy"])))


def load_pointlio_runner():
    names = ("rclpy", "rclpy.node", "rclpy.clock", "rclpy.qos", "std_msgs", "std_msgs.msg",
             "ament_index_python", "ament_index_python.packages")
    modules = {name: types.ModuleType(name) for name in names}
    modules["rclpy.node"].Node = object
    modules["rclpy.clock"].Clock = object
    modules["rclpy.clock"].ClockType = object
    modules["rclpy.qos"].QoSProfile = object
    modules["std_msgs.msg"].String = object
    modules["ament_index_python.packages"].get_package_prefix = lambda _: ""
    spec = importlib.util.spec_from_file_location(
        "rmsim_bridge._mid360_extrinsic_test", ROOT / "ros2_ws/src/rmsim_bridge/rmsim_bridge/point_lio_runner.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class Mid360MountTests(unittest.TestCase):
    def test_official_height_and_connector_axis_preserve_source_installation(self):
        evidence = read("mid360-source.json")
        lidar = read("mid360.json")
        self.assertEqual(evidence["source_parent"], "bone_turret")
        self.assertEqual(evidence["mount_parent_frame"], "yaw_link")
        self.assertEqual(evidence["source_blend_sha256"],
                         "4414ee2f57ef5604b8e9cd8cdf2b2386a424737271ed94c40abbad2e1ce18d37")
        self.assertLess(abs(evidence["measured_body_height_m"] - .060), .0005)
        o, base, z = (evidence[key] for key in
                      ("optical_center_base_m", "mounting_base_center_base_m", "optical_axis_base"))
        for i in range(3):
            # Blender face normals originate as float32; keep the datum check below 20 nm.
            self.assertAlmostEqual(o[i] - base[i], .047 * z[i], delta=2e-8)
            self.assertAlmostEqual(lidar["origin"]["xyz"][i] + evidence["yaw_pivot_base_m"][i], o[i], places=9)
        r = matrix(lidar["origin"]["rpy"])
        for i in range(3):
            self.assertAlmostEqual(r[i][2], z[i], delta=2e-7)
        self.assertAlmostEqual(math.acos(r[2][2]), math.pi / 4, delta=2e-6)
        # Actual connector housing lies on negative sensor X, independently of fitted screw normal.
        basis = evidence["connector_evidence"]["basis_base"]
        housing = evidence["connector_evidence"]["housing"]["midpoint"]
        displacement = [sum(basis[i][j]*housing[j] for j in range(3)) for i in range(3)]
        connector_x = sum(r[i][0]*displacement[i] for i in range(3))
        self.assertLess(connector_x, -.025)
        self.assertEqual(len(evidence["connector_evidence"]["face_screws"]), 4)

    def test_real_pointlio_parameter_builder_recovers_official_signed_extrinsic(self):
        lidar = read("mid360.json")
        imu = next(p for p in read("mid360-imu.json")["imus"] if p["id"] == "mid360_imu")
        state = dict(lidar=dict(runtime_mount(lidar), frame_hz=lidar["frame_hz"]),
                     imu=dict(gravity_z_mps2=-9.81, profiles=[dict(runtime_mount(imu), rate_hz=imu["rate_hz"])]))
        result = load_pointlio_runner().parameters(state, "aim_sentry", "mid360_imu", source_tuning={})
        for actual, expected in zip(result["mapping.extrinsic_T"], [-.011, -.02329, .04412]):
            self.assertAlmostEqual(actual, expected, delta=2e-8)
        for actual, expected in zip(result["mapping.extrinsic_R"], [1., 0., 0., 0., 1., 0., 0., 0., 1.]):
            self.assertAlmostEqual(actual, expected, places=12)
        self.assertEqual(result["mapping.imu_time_inte"], .005)
        self.assertEqual(result["mapping.lidar_time_inte"], .1)
        self.assertEqual(result["mapping.acc_norm"], 9.81)
        self.assertAlmostEqual(result["mapping.satu_acc"], 29.43)
        self.assertEqual(result["mapping.gravity"], [0., 0., -9.81])

    def test_profile_regeneration_is_reproducible_and_preserves_legacy_profiles(self):
        spec = importlib.util.spec_from_file_location("mid360_source_extractor", ROOT / "tools/assets/inspect_mid360_mount.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            original = {p.name: p.read_bytes() for p in PROFILES.glob("*.json")}
            for filename, content in original.items():
                (directory / filename).write_bytes(content)
            module.update_profiles(directory, read("mid360-source.json"))
            self.assertEqual({p.name: p.read_bytes() for p in directory.glob("*.json")}, original)


class Mid360ProfileTests(unittest.TestCase):
    def validator(self, kind, version):
        value = json.loads((ROOT / f"schemas/{kind}-runtime-v{version}.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(value)
        return Draft202012Validator(value)

    def test_active_and_ideal_profiles_validate_and_share_mechanical_datums(self):
        active, ideal = read("mid360.json"), read("mid360-ideal.json")
        self.validator("lidar", 3).validate(active)
        self.validator("lidar", 2).validate(ideal)
        self.assertEqual(active["origin"], ideal["origin"])
        self.assertEqual(active["rays_per_second"], 200000)
        self.assertEqual(active["frame_hz"], 10)
        self.assertAlmostEqual(math.degrees(active["elevation_min_rad"]), -7)
        self.assertAlmostEqual(math.degrees(active["elevation_max_rad"]), 52)
        self.assertEqual(active["mid360"]["pending_scan_limit"], 8)
        self.assertIs(active["mid360"]["provenance"]["measurement_calibrated"], False)
        active_imus, ideal_imus = read("mid360-imu.json"), read("mid360-imu-ideal.json")
        self.validator("imu", 3).validate(active_imus)
        self.validator("imu", 2).validate(ideal_imus)
        for physical, deterministic in zip(active_imus["imus"], ideal_imus["imus"]):
            self.assertEqual(physical["origin"], deterministic["origin"])
            self.assertEqual(physical["rate_hz"], 200)
            self.assertEqual(physical["accel_noise_std_mps2"], deterministic["accel_noise_std_mps2"])
            self.assertEqual(physical["gyro_noise_std_rad_s"], deterministic["gyro_noise_std_rad_s"])
            self.assertNotIn("measurement", deterministic)

    def test_profile_schema_rejects_fabricated_rates_fov_and_calibration(self):
        validator = self.validator("lidar", 3)
        for path, invalid in [(("rays_per_second",), 800000), (("frame_hz",), 30),
                              (("elevation_min_rad",), 0), (("elevation_max_rad",), math.pi/2),
                              (("max_range_m",), 100), (("calibrated",), True),
                              (("mid360", "pending_scan_limit"), 16),
                              (("mid360", "nir_default_reflectance"), 1.5),
                              (("mid360", "provenance", "measurement_calibrated"), True)]:
            with self.subTest(path=path):
                value = read("mid360.json")
                target = value
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = invalid
                self.assertFalse(validator.is_valid(value))
        for hz in (5, 10, 20, 50, 100):
            value = read("mid360.json")
            value["frame_hz"] = hz
            validator.validate(value)

    def test_imu_limiting_is_explicit_si_and_ideal_channels_remain_unlimited(self):
        profiles = {p["id"]: p for p in read("mid360-imu.json")["imus"]}
        self.assertEqual(profiles["imu"]["measurement"]["accel_range_mps2"], 0)
        self.assertEqual(profiles["imu"]["measurement"]["gyro_range_rad_s"], 0)
        device = profiles["mid360_imu"]["measurement"]
        self.assertAlmostEqual(device["accel_range_mps2"], 16 * 9.80665)
        self.assertAlmostEqual(device["gyro_range_rad_s"], math.radians(2000))
        self.assertEqual(device["accel_quantization_mps2"], 0)
        self.assertEqual(device["gyro_quantization_rad_s"], 0)
        value = read("mid360-imu.json")
        value["imus"][1]["measurement"]["accel_quantization_mps2"] = -.1
        self.assertFalse(self.validator("imu", 3).is_valid(value))


if __name__ == "__main__":
    unittest.main()
