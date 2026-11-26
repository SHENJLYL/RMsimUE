import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


LAUNCHER = Path(__file__).resolve().parents[2] / "tools/launcher/rmsim_launcher.py"
spec = importlib.util.spec_from_file_location("rmsim_launcher", LAUNCHER)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def variable(value):
    return SimpleNamespace(get=lambda: value)


class MatchLauncherTest(unittest.TestCase):
    def make_match(self, primary, red, blue, auto=True, preparation=False):
        commands = []
        roster = {
            team: [{"role": "S7 哨兵", "vehicle_id": "aim_sentry", "control": control}]
            for team, control in (("red", red), ("blue", blue))
        }
        app = SimpleNamespace(
            maps=[{"id": "rmuc2027", "path": "map.json", "team_spawns": {"red": "red", "blue": "default"}}],
            vehicles=[{"id": "aim_sentry", "path": "aim_sentry"}],
            match_roster=roster,
            match_auto_sentry_var=variable(auto),
            match_preparation_var=variable(preparation),
            port_var=variable("9541"),
            domain_var=variable("72"),
            robot_id_var=variable("aim_sentry"),
            camera_hz_var=variable("0"),
            scale_var=variable("50"),
            nav_yaml_var=variable(""),
            _ensure_valid_pilot=lambda: (primary, 0),
            _external_sentry_command=launcher.RMSimLauncherApp._external_sentry_command,
            _log=lambda _message: None,
            _run_background=lambda cmd, _title: commands.append(cmd),
        )
        self.addCleanup(patch.stopall)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patch.object(launcher, "REPO_ROOT", Path(temp.name)).start()
        warning = patch.object(launcher.messagebox, "showwarning").start()
        launcher.RMSimLauncherApp._launch_match(app)
        return app, commands, warning

    def test_blue_manual_and_red_ros_opponent_are_independent(self):
        app, [cmd], warning = self.make_match("blue", "sentry_ws", "manual")
        warning.assert_not_called()
        self.assertEqual(cmd[cmd.index("-SpawnId") + 1], "default")
        self.assertEqual(cmd[cmd.index("-RosVehicleTeam") + 1], "red")
        self.assertEqual(cmd[cmd.index("-CameraHz") + 1], "15")
        self.assertTrue({"-RosControl", "-RosAimControl", "-RosImu", "-RosLidar", "-AutoSentry"} <= set(cmd))
        path = Path(cmd[cmd.index("-MatchRoster") + 1])
        self.assertTrue(path.is_absolute())
        roster = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((roster["primary_team"], roster["primary_control"], roster["ros_team"]),
                         ("blue", "manual", "red"))
        self.assertEqual(app.match_connector[app.match_connector.index("-SpawnId") + 1], "red")

    def test_primary_ros_spectator_keeps_selected_seat(self):
        app, [cmd], warning = self.make_match("red", "sentry_ws", "manual", auto=False)
        warning.assert_not_called()
        self.assertEqual(cmd[cmd.index("-SpawnId") + 1], "red")
        self.assertEqual(cmd[cmd.index("-RosVehicleTeam") + 1], "red")
        self.assertNotIn("-AutoSentry", cmd)
        self.assertFalse(app.match_owns_connector)

    def test_red_manual_and_blue_ros_use_distinct_spawns(self):
        app, [cmd], warning = self.make_match("red", "manual", "sentry_ws")
        warning.assert_not_called()
        self.assertEqual(cmd[cmd.index("-SpawnId") + 1], "red")
        self.assertEqual(cmd[cmd.index("-RosVehicleTeam") + 1], "blue")
        self.assertEqual(app.match_connector[app.match_connector.index("-SpawnId") + 1], "default")

    def test_manual_match_keeps_roster_without_ros(self):
        app, [cmd], warning = self.make_match("blue", "manual", "manual")
        warning.assert_not_called()
        self.assertIn("-MatchRoster", cmd)
        self.assertNotIn("-RosControl", cmd)
        self.assertNotIn("-AutoSentry", cmd)
        self.assertIsNone(app.match_connector)
        self.assertNotIn("-MatchPreparation", cmd)

    def test_preparation_passes_the_tray_loading_option(self):
        _, [cmd], warning = self.make_match("blue", "manual", "manual", preparation=True)
        warning.assert_not_called()
        self.assertIn("-MatchPreparation", cmd)

    def test_two_ros_competitors_do_not_start_with_a_single_bridge(self):
        _, commands, warning = self.make_match("red", "sentry_ws", "sentry_ws")
        self.assertEqual(commands, [])
        warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()

