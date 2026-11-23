"""Release evidence rejects stale map packages and staging/formal statistic mixing."""
from __future__ import annotations
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"tools/integration"))
import summarize_navigation_realtime as gate


def formal_fixture():
    identity = ["native-session", 3]
    stage_id, goal_id = "a"*32, "b"*32
    start_position = [-8.5, 0., 0.]
    staged = dict(phase="staging", session_identity=identity, accepted=True,
                  frame_id="aim_sentry/map", goal_id_hex=stage_id,
                  goal_position_m=[-8.5, 0., 0.], result_status=4,
                  requested_stamp=1., accepted_stamp=1.1, result_stamp=7.5,
                  requested_wall=101., accepted_wall=101.1, result_wall=107.5)
    action = dict(phase="formal", session_identity=identity, accepted=True,
                  frame_id="aim_sentry/map", goal_id_hex=goal_id,
                  goal_position_m=[8.5, 0., 0.], result_status=5,
                  requested_stamp=10., accepted_stamp=10.01, result_stamp=11.6,
                  requested_wall=110.01, accepted_wall=110.02, result_wall=111.6)
    truth = [dict(phase="staging", session_identity=identity, stamp=8.+i*.1,
                  wall=108.+i*.1, position=list(start_position), speed_mps=0.)
             for i in range(11)]
    formal_truth = []
    for i in range(21):
        stamp = 10.1+i*.1
        row = dict(phase="formal", session_identity=identity, stamp=stamp,
                   wall=100.+stamp, position=[-8.5+4.2*(stamp-10.), 0., 0.])
        if formal_truth:
            first = formal_truth[-1]
            row["speed_mps"] = (row["position"][0]-first["position"][0])/(stamp-first["stamp"])
        formal_truth.append(row)
    truth += formal_truth
    drive = dict(applied_requested_world_linear_m_s=[4.2,0.,0.],
                 filtered_world_linear_m_s=[4.2,0.,0.],
                 applied_requested_yaw_rad_s=0., filtered_yaw_rad_s=0.,
                 applied_velocity_command_mode="external_shaped_velocity")
    drives = [dict(phase="formal", session_identity=identity, stamp=10.2+i*.05,
                   drive=copy.deepcopy(drive)) for i in range(20)]
    commands = [dict(phase="formal", session_identity=identity, stamp=10.2+i*.05,
                     linear=[4.2,0.], yaw=0.) for i in range(20)]
    trace = dict(schema_version=2, actions=[staged, action], truth=truth, drive=drives,
                 commands=commands, plans=[dict(phase="formal", session_identity=identity,
                                                stamp=10.2, points=20)])
    peak = max(r.get("speed_mps", 0.) for r in formal_truth)
    report = dict(schema_version=2, actions=copy.deepcopy(trace["actions"]),
        contract=dict(fixture_version=2, start=[-8.5,0.], staging_goal=[-8.5,0.],
                      spawn_start=[0.,0.], goal=[8.5,0.], source_cap_mps=5.,
                      speed_threshold_mps=4., sustained_seconds=.3),
        staging=dict(session_identity=identity, goal_id_hex=stage_id, action_status=4,
            action_succeeded=True, goal_m=[-8.5,0.], actual_position_m=start_position,
            position_tolerance_m=.25, actual_error_m=0., within_position_tolerance=True,
            stop_speed_limit_mps=.05, speed_mps=0., required_stationary_seconds=.5,
            stationary_seconds=1., stopped=True, stopped_start_stamp=8., stopped_confirmed_stamp=9.),
        formal_measurement=dict(session_identity=identity, goal_id_hex=goal_id,
            measurement_phase="formal", start_stamp=10., end_stamp=formal_truth[-1]["stamp"],
            start_wall=110., end_wall=100.+formal_truth[-1]["stamp"],
            actual_start_position_m=list(start_position), truth_samples=21, drive_samples=20,
            command_samples=20, actual_peak_speed_mps=peak, command_peak_speed_mps=4.2,
            sustained=dict(start_stamp=formal_truth[1]["stamp"], confirmed_stamp=formal_truth[5]["stamp"],
                           threshold_mps=4., required_duration_s=.3)),
        actual_peak_speed_mps=peak, checks=[
            dict(name="source_command_cap_preserved", passed=True, observed_command_peak_mps=4.2),
            dict(name="source_command_reaches_wheel_target_without_second_shaper", passed=True,
                 samples=20, max_linear_error_mps=0., max_yaw_error_rad_s=0.)])
    stop = formal_truth[17]["stamp"]
    begin = formal_truth[12]["stamp"]
    selected = [r for r in formal_truth if begin <= r["stamp"] <= stop]
    distance = sum(b["position"][0]-a["position"][0] for a,b in zip(selected,selected[1:]))
    report["cancellation"] = dict(stamp=begin, stop_confirmed_stamp=stop,
                                 actual_stop_distance_m=distance, distance_bound_m=3.)
    return report, trace, identity


class NavigationReleaseEvidenceTests(unittest.TestCase):
    def assert_phase_passes(self, report, trace, identity):
        checks, metrics = gate.top_speed_phase_checks(report, trace, identity)
        self.assertTrue(all(checks.values()), (checks, metrics))

    def test_formal_raw_positions_and_targets_reproduce_pass(self):
        self.assert_phase_passes(*formal_fixture())

    def test_staging_peak_cannot_be_used_as_formal_peak(self):
        report, trace, identity = formal_fixture()
        trace["truth"].insert(0, dict(phase="staging", session_identity=identity, stamp=7.,
                                     position=[-9.,0.,0.], wall=107., speed_mps=99.))
        self.assert_phase_passes(report, trace, identity)
        report["actual_peak_speed_mps"] = report["formal_measurement"]["actual_peak_speed_mps"] = 99.
        checks, _ = gate.top_speed_phase_checks(report, trace, identity)
        self.assertFalse(checks["top_speed_peak_and_sustained_speed_reproduced_only_from_formal_truth"])

    def test_delayed_preformal_headers_excluded_even_with_formal_receive_phase(self):
        report, trace, identity = formal_fixture()
        trace["commands"].insert(0, dict(phase="formal", session_identity=identity, stamp=9.9,
                                        linear=[99.,0.], yaw=0.))
        self.assert_phase_passes(report, trace, identity)
        report["formal_measurement"]["command_samples"] += 1
        report["formal_measurement"]["command_peak_speed_mps"] = 99.
        checks, _ = gate.top_speed_phase_checks(report, trace, identity)
        self.assertFalse(checks["top_speed_formal_samples_exclude_staging_and_keep_session_identity"])
        self.assertFalse(checks["top_speed_formal_command_peak_preserves_source_cap"])

    def test_claimed_tracking_pass_cannot_hide_raw_second_shaper(self):
        report, trace, identity = formal_fixture()
        trace["drive"][0]["drive"]["filtered_world_linear_m_s"][0] -= .01
        checks, _ = gate.top_speed_phase_checks(report, trace, identity)
        self.assertFalse(checks["top_speed_formal_drive_targets_reproduce_unshaped_tracking"])

    def test_packaged_default_requires_current_manifest_binding(self):
        old_root = gate.ROOT
        try:
            with tempfile.TemporaryDirectory() as temp:
                gate.ROOT = Path(temp)
                manifest = gate.ROOT/"assets/maps/rmuc2027/navigation-console/SOURCE.json"
                manifest.parent.mkdir(parents=True)
                manifest.write_text('{"version":1}', encoding="utf-8")
                selected = manifest.parent/"localization/map.yaml"
                pair = {"identity":"map-pair"}
                helper = SimpleNamespace(
                    packaged_localization_map=lambda *args: dict(yaml_path=str(selected), missing_derived=[], files={}),
                    validate_localization_map=lambda *args: pair)
                provenance = dict(localization_map=pair, scene=dict(scene="rmuc2027", map_yaml="navigation.yaml",
                    localization_map_selection="package_default", localization_map_yaml=str(selected),
                    localization_map_package=dict(manifest_path=str(manifest), manifest_sha256=gate.digest(manifest))))
                checks, _ = gate.localization_map_checks(provenance, selected, helper)
                self.assertTrue(all(checks.values()), checks)
                manifest.write_text('{"version":2}', encoding="utf-8")
                checks, _ = gate.localization_map_checks(provenance, selected, helper)
                self.assertFalse(checks["localization_map_selection_and_current_package_match"])
        finally:
            gate.ROOT = old_root

    def test_explicit_map_skips_unused_default_and_single_map_rejects_active_default(self):
        old_root = gate.ROOT
        try:
            with tempfile.TemporaryDirectory() as temp:
                gate.ROOT = Path(temp)
                selected = gate.ROOT/"explicit.yaml"
                pair = {"identity":"explicit"}
                def forbidden(*args):
                    raise AssertionError("Explicit selection must not inspect the default package")
                helper = SimpleNamespace(packaged_localization_map=forbidden,
                                         validate_localization_map=lambda *args: pair)
                provenance = dict(localization_map=pair, scene=dict(scene="rmuc2027", map_yaml="nav.yaml",
                    localization_map_selection="explicit", localization_map_yaml=str(selected),
                    localization_map_package=None))
                checks, _ = gate.localization_map_checks(provenance, selected, helper)
                self.assertTrue(all(checks.values()), checks)
                manifest = gate.ROOT/"assets/maps/rmuc2027/navigation-console/SOURCE.json"
                manifest.parent.mkdir(parents=True)
                manifest.write_text("{}", encoding="utf-8")
                helper.packaged_localization_map = lambda *args: {"yaml_path": str(selected)}
                provenance["localization_map"] = None
                provenance["scene"].update(localization_map_selection="single_map", localization_map_yaml="")
                checks, _ = gate.localization_map_checks(provenance, None, helper)
                self.assertFalse(checks["localization_map_selection_and_current_package_match"])
        finally:
            gate.ROOT = old_root


if __name__ == "__main__":
    unittest.main()
