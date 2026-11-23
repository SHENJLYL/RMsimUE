"""Scene-owned AMCL startup prior contracts; no ROS, UE or evaluation input."""
import copy
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/integration"))
sys.path.insert(0, str(ROOT / "src"))
import navigation_console_config as config


class SceneSpawnPriorTests(unittest.TestCase):
    def scene(self, position=None, yaw=0.):
        return {
            "units": "m", "coordinate_system": "ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP",
            "spawns": [{"id": "default", "position_m": position or [3., -1., 1.7], "yaw_rad": yaw}],
        }

    def source_scene(self, name):
        relative = "assets/maps/rmuc2027/navigation-console/scene.json" if name == "rmuc2027" else "assets/maps/import_lab/map.json"
        return json.loads((ROOT / relative).read_text(encoding="utf-8-sig"))

    def test_actual_rmuc_default_uses_declared_spawn_without_old_recovery_bias(self):
        initial, prior = config.scene_spawn_prior(self.source_scene("rmuc2027"))
        self.assertEqual(initial, [5.8, 6.4, 0.])
        self.assertEqual(prior["mode"], "scene_spawn")
        self.assertIsNone(prior["requested_offset"])
        self.assertEqual(prior["applied_offset"], [0., 0., 0.])
        self.assertEqual(prior["spawn_position_m"], [5.8, 6.4, .03147864])
        self.assertEqual(prior["frame_semantics"]["reference_origin"], "base_link_and_base_stabilized_common_origin")

    def test_actual_import_lab_and_complete_preparation_use_default_not_blue_spawn(self):
        scene = self.source_scene("import_lab")
        scene["spawns"].reverse()
        self.assertEqual(config.scene_spawn_prior(scene)[0], [0., 0., 0.])
        with tempfile.TemporaryDirectory() as temporary:
            prepared = config.prepare_scene("import_lab", Path(temporary) / "prepared")
            self.assertEqual(prepared["initial_pose"], [0., 0., 0.])
            self.assertEqual(prepared["initial_pose_prior"]["initial_pose"], prepared["initial_pose"])
            self.assertEqual(prepared["localization_map_selection"], "single_map")
            self.assertTrue(Path(prepared["map_yaml"]).is_file())

    def test_previous_perturbed_prior_is_explicit_and_reproduces_both_scenes(self):
        for name, expected in [("rmuc2027", [6.4, 6., .15]), ("import_lab", [.6, -.4, .15])]:
            with self.subTest(scene=name):
                initial, prior = config.scene_spawn_prior(self.source_scene(name), [.6, -.4, .15])
                for actual, wanted in zip(initial, expected):
                    self.assertAlmostEqual(actual, wanted, places=12)
                self.assertEqual(prior["mode"], "scene_spawn_with_explicit_offset")
                self.assertEqual(prior["requested_offset"], [.6, -.4, .15])

    def test_translation_offset_stays_in_map_axes_at_nonzero_heading(self):
        initial, prior = config.scene_spawn_prior(self.scene(yaw=math.pi / 2), [.6, -.4, .15])
        self.assertAlmostEqual(initial[0], 3.6)
        self.assertAlmostEqual(initial[1], -1.4)
        self.assertAlmostEqual(initial[2], math.pi / 2 + .15)
        self.assertEqual(prior["spawn_position_m"][2], 1.7)
        self.assertEqual(prior["frame_semantics"]["ground_z_use"], "provenance_only_planar_prior")

    def test_yaw_wraps_across_pi_and_retains_requested_turns(self):
        requested = 2 * math.tau + .2
        initial, prior = config.scene_spawn_prior(self.scene(yaw=math.pi - .05), [0., 0., requested])
        self.assertAlmostEqual(initial[2], -math.pi + .15)
        self.assertEqual(prior["requested_offset"][2], requested)
        self.assertAlmostEqual(prior["applied_offset"][2], .2)
        self.assertEqual(config.scene_spawn_prior(self.scene(yaw=math.pi))[0][2], -math.pi)

    def test_default_and_explicit_zero_are_distinguishable_and_snapshotted(self):
        scene = self.scene()
        untouched = copy.deepcopy(scene)
        initial, prior = config.scene_spawn_prior(scene, [0., 0., 0.])
        self.assertEqual(scene, untouched)
        self.assertEqual(prior["mode"], "scene_spawn_with_explicit_offset")
        scene["spawns"][0]["position_m"][0] = 999.
        initial[0] = 77.
        self.assertEqual(prior["spawn_position_m"], [3., -1., 1.7])
        self.assertEqual(prior["initial_pose"], [3., -1., 0.])

    def test_missing_duplicate_or_malformed_spawn_identity_is_rejected(self):
        for spawns in [None, [], {}, [None], [{"id": "blue"}],
                       [{"id": "default"}, {"id": "default"}],
                       [{"id": "default "}], [{"id": "../default"}], [{"id": 1}],
                       [{"id": "Default"}]]:
            with self.subTest(spawns=spawns):
                scene = self.scene(); scene["spawns"] = spawns
                with self.assertRaises(ValueError):
                    config.scene_spawn_prior(scene)

    def test_unknown_units_and_axes_are_rejected(self):
        for key, value in [("units", "cm"), ("coordinate_system", "UNREAL_LEFT_HANDED"), ("units", None)]:
            with self.subTest(field=key, value=value):
                scene = self.scene(); scene[key] = value
                with self.assertRaisesRegex(ValueError, "metres and ROS"):
                    config.scene_spawn_prior(scene)

    def test_nonfinite_or_nonnumeric_spawn_and_offsets_are_rejected(self):
        for bad in [float("nan"), float("inf"), float("-inf"), True, "0.1", None, 10**400]:
            for location in ["position", "yaw", "offset"]:
                with self.subTest(value=str(bad)[:30], location=location):
                    scene = self.scene(); offset = None
                    if location == "position": scene["spawns"][0]["position_m"][2] = bad
                    if location == "yaw": scene["spawns"][0]["yaw_rad"] = bad
                    if location == "offset": offset = [0., bad, 0.]
                    with self.assertRaisesRegex(ValueError, "finite number"):
                        config.scene_spawn_prior(scene, offset)
        with self.assertRaisesRegex(ValueError, "initial_pose.x"):
            config.scene_spawn_prior(self.scene([1.e308, 0., 0.]), [1.e308, 0., 0.])

    def test_malformed_vector_lengths_fail_before_native_launch(self):
        for value in [[], [1., 2.], [1., 2., 3., 4.], "123", {"x": 1}]:
            with self.subTest(value=value):
                scene = self.scene(); scene["spawns"][0]["position_m"] = value
                with self.assertRaisesRegex(ValueError, "three finite numbers"):
                    config.scene_spawn_prior(scene)
                with self.assertRaisesRegex(ValueError, "three finite numbers"):
                    config.scene_spawn_prior(self.scene(), value)

    def test_console_cli_exposes_optional_three_component_offset_without_ros(self):
        script = ROOT / "tools/integration/navigation_console.py"
        result = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--initial-pose-offset DX_M DY_M DYAW_RAD", result.stdout)
        result = subprocess.run([sys.executable, str(script), "--initial-pose-offset", ".6", "-.4"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("expected 3 arguments", result.stderr)


if __name__ == "__main__":
    unittest.main()
