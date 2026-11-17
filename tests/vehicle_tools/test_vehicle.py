import copy
import hashlib
import json
import math
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rm_vehicle_tools import VehicleError, load_vehicle
from rm_vehicle_tools.__main__ import main as cli_main
from rm_vehicle_tools.vehicle import read_limited


class VehicleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.package = Path(self.temp.name) / "vehicle"
        shutil.copytree(ROOT / "assets/vehicles/sentry_omni", self.package)
        self.manifest_path = self.package / "vehicle.json"
        self.urdf_path = self.package / "model/robot.urdf"

    def change_manifest(self, change):
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        change(data)
        self.manifest_path.write_text(json.dumps(data), encoding="utf-8")

    def change_urdf(self, change):
        tree = ET.parse(self.urdf_path)
        change(tree.getroot())
        tree.write(self.urdf_path, encoding="utf-8", xml_declaration=True)

    def invalid(self, message):
        with self.assertRaisesRegex(VehicleError, message):
            load_vehicle(self.package)

    def cli(self, *args):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run([sys.executable, "-m", "rm_vehicle_tools", *map(str, args)],
                              capture_output=True, text=True, env=env, cwd=ROOT)

    def test_examples_have_different_geometry_and_complete_sensor_rigs(self):
        omni = load_vehicle(ROOT / "assets/vehicles/sentry_omni")
        diff = load_vehicle(ROOT / "assets/vehicles/training_diff/vehicle.json")
        self.assertEqual((len(omni["links"]), len(diff["links"])), (7, 5))
        self.assertEqual(len(omni["control"]["wheel_joints"]), 4)
        self.assertEqual(len(diff["control"]["wheel_joints"]), 2)
        self.assertNotEqual(omni["links"][0]["collisions"], diff["links"][0]["collisions"])
        for data in (omni, diff):
            self.assertEqual({s["type"] for s in data["sensors"]}, {"camera", "imu", "lidar"})
            self.assertEqual(data["units"]["length"], "m")
            self.assertEqual(data["coordinate_system"], "ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP")
            self.assertEqual(data["control"]["gimbal"]["pitch_joint"], "pitch_joint")
            self.assertEqual(data["root_link"], "base_link")

    def test_reads_are_bounded_before_size_rejection(self):
        from unittest.mock import MagicMock
        stream = MagicMock()
        stream.__enter__.return_value = stream
        stream.read.return_value = b"x" * 33
        with patch.object(Path, "open", return_value=stream):
            with self.assertRaisesRegex(VehicleError, "size limit"):
                read_limited(self.manifest_path, 32, "manifest")
        stream.read.assert_called_once_with(33)

    def test_schema_rejects_nonportable_path_characters(self):
        schema = json.loads((ROOT / "schemas/vehicle-v1.schema.json").read_text())
        rule = schema["properties"]["model"]["properties"]["path"]
        for bad in ("model/robot?.urdf", "model/a|b.urdf", "model/a\tb.urdf"):
            self.assertIsNotNone(re.search(rule["not"]["pattern"], bad))
            self.change_manifest(lambda d: d["model"].update(path=bad))
            self.invalid("portable path")

    def test_source_hash_and_deterministic_export(self):
        data = load_vehicle(self.package)
        self.assertEqual(data["source"]["manifest_sha256"], hashlib.sha256(self.manifest_path.read_bytes()).hexdigest())
        self.assertEqual(data["source"]["urdf_sha256"], hashlib.sha256(self.urdf_path.read_bytes()).hexdigest())
        self.assertEqual(data, load_vehicle(self.package))
        target = Path(self.temp.name) / "canonical.json"
        result = self.cli("export", self.package, "--output", target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), data)

    def test_internal_manifest_link_preserves_package_root_and_source_files(self):
        # Model an in-package file symlink without requiring Windows link privilege.
        actual_manifest = self.package / "config" / "shared.json"
        actual_manifest.parent.mkdir()
        shutil.copyfile(self.manifest_path, actual_manifest)
        original_resolve = Path.resolve
        entry = self.manifest_path

        def resolve(path, *args, **kwargs):
            if path == entry:
                return original_resolve(actual_manifest, *args, **kwargs)
            return original_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", resolve):
            self.assertEqual(load_vehicle(entry)["root_link"], "base_link")
            for source in (actual_manifest, self.urdf_path):
                before = source.read_bytes()
                with self.subTest(source=source):
                    self.assertEqual(cli_main(["export", str(entry), "--output", str(source)]), 2)
                    self.assertEqual(source.read_bytes(), before)

    def test_mesh_references_remain_local_and_are_hashed(self):
        mesh = self.package / "model/shape.obj"
        mesh.write_text("v 0 0 0\n", encoding="utf-8")
        def mutate(robot):
            geometry = robot.find("link/visual/geometry")
            geometry.clear()
            ET.SubElement(geometry, "mesh", filename="shape.obj", scale="1 2 3")
        self.change_urdf(mutate)
        data = load_vehicle(self.package)
        geometry = data["links"][0]["visuals"][0]["geometry"]
        self.assertEqual(geometry, {"type": "mesh", "path": "model/shape.obj", "scale": [1.0, 2.0, 3.0]})
        self.assertEqual(data["source"]["assets"], [{"path": "model/shape.obj", "sha256": hashlib.sha256(mesh.read_bytes()).hexdigest()}])

    def test_manifest_paths_reject_escape_absolute_and_urls(self):
        for bad in ("../robot.urdf", "/tmp/robot.urdf", "C:/robot.urdf", "model\\robot.urdf", "https://host/model", "file://model", "model/./robot.urdf", "model//robot.urdf", "model/\x00.urdf"):
            with self.subTest(path=bad):
                self.change_manifest(lambda d: d["model"].update(path=bad))
                self.invalid("model.path")

    def test_mesh_path_escape_is_rejected(self):
        def mutate(robot):
            geometry = robot.find("link/visual/geometry")
            geometry.clear()
            ET.SubElement(geometry, "mesh", filename="../vehicle.json")
        self.change_urdf(mutate)
        self.invalid("mesh.filename")

    def test_symlink_asset_escape_is_rejected(self):
        outside = Path(self.temp.name) / "outside.urdf"
        shutil.copyfile(self.urdf_path, outside)
        self.urdf_path.unlink()
        try:
            self.urdf_path.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        self.invalid("path escapes package")

    def test_manifest_symlink_escape_is_rejected_for_file_entry(self):
        outside = Path(self.temp.name) / "outside" / "vehicle.json"
        outside.parent.mkdir()
        shutil.copyfile(self.manifest_path, outside)
        self.manifest_path.unlink()
        try:
            self.manifest_path.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        with self.assertRaisesRegex(VehicleError, "path escapes package"):
            load_vehicle(self.manifest_path)

    @unittest.skipUnless(sys.platform == "win32", "Windows junction containment check")
    def test_windows_junction_escape_is_rejected(self):
        import _winapi
        outside = Path(self.temp.name) / "outside_model"
        outside.mkdir()
        shutil.copyfile(self.urdf_path, outside / "robot.urdf")
        junction = self.package / "linked_model"
        _winapi.CreateJunction(str(outside), str(junction))
        # Remove only the junction, before TemporaryDirectory cleans its own tree.
        self.addCleanup(os.rmdir, junction)
        self.change_manifest(lambda d: d["model"].update(path="linked_model/robot.urdf"))
        self.invalid("path escapes package")

    def test_duplicate_json_key_and_boolean_version_rejected(self):
        self.manifest_path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        self.invalid("duplicate key")
        shutil.copyfile(ROOT / "assets/vehicles/sentry_omni/vehicle.json", self.manifest_path)
        self.change_manifest(lambda d: d.update(schema_version=True))
        self.invalid("schema_version")

    def test_nonfinite_and_boolean_numbers_rejected(self):
        for value in (float("nan"), float("inf"), True, -1, 0):
            with self.subTest(value=value):
                self.change_manifest(lambda d: d["control"]["limits"].update(linear_mps=value))
                self.invalid("linear_mps")

    def test_unknown_fields_rejected(self):
        self.change_manifest(lambda d: d["control"].update(linear_limit=3))
        self.invalid("unsupported keys")

    def test_sensor_validation(self):
        original = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        cases = [
            (lambda d: d["sensors"].append(copy.deepcopy(d["sensors"][0])), "duplicate"),
            (lambda d: d["sensors"][0].update(parent_link="missing"), "unknown parent_link"),
            (lambda d: d["sensors"][0]["parameters"].update(width=True), "positive integer"),
            (lambda d: d["sensors"][0]["parameters"].update(horizontal_fov_rad=math.pi), "FOV"),
            (lambda d: d["sensors"][2]["parameters"].update(range_min_m=100), "range_min_m"),
            (lambda d: d["sensors"][0]["origin"].update(xyz=[0, 1]), "3 numbers"),
        ]
        for change, message in cases:
            with self.subTest(message=message):
                self.manifest_path.write_text(json.dumps(original), encoding="utf-8")
                self.change_manifest(change)
                self.invalid(message)

    def test_control_references_and_duplicate_roles_rejected(self):
        self.change_manifest(lambda d: d["control"]["wheel_joints"].__setitem__(0, "missing"))
        self.invalid("must name a continuous joint")
        self.change_manifest(lambda d: d["control"]["wheel_joints"].__setitem__(0, "wheel_0_joint"))
        self.change_manifest(lambda d: d["control"]["gimbal"].update(yaw_joint="wheel_0_joint"))
        self.invalid("already a wheel")

    def test_dtd_and_entity_rejected_without_access(self):
        self.urdf_path.write_text('<!DOCTYPE robot [<!ENTITY ext SYSTEM "file:///sensitive">]><robot name="r">&ext;</robot>', encoding="utf-8")
        self.invalid("DTD and entities")

    def test_duplicate_link_and_joint_names_rejected(self):
        original = self.urdf_path.read_bytes()
        self.change_urdf(lambda r: r.append(copy.deepcopy(r.find("link"))))
        self.invalid("link: duplicate")
        self.urdf_path.write_bytes(original)
        self.change_urdf(lambda r: r.append(copy.deepcopy(r.find("joint"))))
        self.invalid("joint: duplicate")

    def test_disconnected_cycle_rejected_even_with_one_root(self):
        def mutate(robot):
            robot.find("joint[@name='wheel_0_joint']/parent").set("link", "wheel_1_link")
            robot.find("joint[@name='wheel_1_joint']/parent").set("link", "wheel_0_link")
        self.change_urdf(mutate)
        self.invalid("joint cycle")

    def test_multiple_roots_and_missing_parent_rejected(self):
        original = self.urdf_path.read_bytes()
        self.change_urdf(lambda r: r.remove(r.find("joint")))
        self.invalid("exactly one root")
        self.urdf_path.write_bytes(original)
        self.change_urdf(lambda r: r.find("joint/parent").set("link", "missing"))
        self.invalid("unknown parent")

    def test_physical_inertia_validation(self):
        original = self.urdf_path.read_bytes()
        for attr, value, message in [("ixx", "-1", "positive definite"), ("ixx", "1000", "moment inequalities"), ("ixy", "nan", "finite number")]:
            with self.subTest(attr=attr, value=value):
                self.urdf_path.write_bytes(original)
                self.change_urdf(lambda r: r.find("link/inertial/inertia").set(attr, value))
                self.invalid(message)
        self.urdf_path.write_bytes(original)
        self.change_urdf(lambda r: r.find("link/inertial/mass").set("value", "0"))
        self.invalid("mass: must be positive")

    def test_geometry_and_joint_limits_rejected(self):
        original = self.urdf_path.read_bytes()
        self.change_urdf(lambda r: r.find("link/visual/geometry/box").set("size", "0 1 1"))
        self.invalid("box.size")
        self.urdf_path.write_bytes(original)
        self.change_urdf(lambda r: r.find("joint[@name='pitch_joint']/limit").set("lower", "5"))
        self.invalid("lower must be less")
        self.urdf_path.write_bytes(original)
        self.change_urdf(lambda r: r.find("joint/axis").set("xyz", "0 0 0"))
        self.invalid("invalid axis")

    def test_fixed_joint_and_sphere_supported(self):
        def mutate(robot):
            link = copy.deepcopy(robot.find("link"))
            link.set("name", "sensor_mount")
            for geometry in link.findall("*/geometry"):
                geometry.clear(); ET.SubElement(geometry, "sphere", radius="0.1")
            robot.append(link)
            joint = ET.SubElement(robot, "joint", name="sensor_mount_joint", type="fixed")
            ET.SubElement(joint, "parent", link="base_link")
            ET.SubElement(joint, "child", link="sensor_mount")
        self.change_urdf(mutate)
        data = load_vehicle(self.package)
        self.assertEqual(data["joints"][-1]["type"], "fixed")
        self.assertEqual(data["links"][-1]["visuals"][0]["geometry"]["type"], "sphere")

    def test_unsupported_urdf_semantics_are_not_silently_dropped(self):
        self.change_urdf(lambda r: ET.SubElement(r.find("joint"), "mimic", joint="yaw_joint"))
        self.invalid("unsupported child")

    def test_cli_failure_keeps_existing_output_and_leaves_no_partial(self):
        output = Path(self.temp.name) / "output.json"
        output.write_text("existing", encoding="utf-8")
        self.change_manifest(lambda d: d.update(schema_version=2))
        result = self.cli("export", self.package, "--output", output)
        self.assertEqual(result.returncode, 2)
        self.assertIn("schema_version", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(output.read_text(), "existing")
        self.assertEqual(list(output.parent.glob(".vehicle-*.tmp")), [])

    def test_cli_protects_source_and_inspects_valid_package(self):
        original = self.manifest_path.read_bytes()
        result = self.cli("export", self.package, "--output", self.manifest_path)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.manifest_path.read_bytes(), original)
        result = self.cli("inspect", self.package)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["id"], "sentry_omni")
        self.assertEqual(self.cli("validate", self.package).returncode, 0)


if __name__ == "__main__":
    unittest.main()
