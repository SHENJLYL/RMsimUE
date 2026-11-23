"""Combine explicitly selected native realtime/navigation evidence without rerunning it.

Each selected run must match the current built module, installed bridge and
vehicle/source configuration. Missing, failed or mismatched evidence produces
in_progress. Historical failures remain linked and are never overwritten.

Example (run in the project Python environment with PyYAML):
  python tools/integration/summarize_navigation_realtime.py \
    --wave-session artifacts/navigation/realtime-20261118/rmuc-final \
    --top-speed-session artifacts/navigation/realtime-20261118/speed-final \
    --top-speed-report artifacts/navigation/realtime-20261118/speed-final/top-speed \
    --clock artifacts/navigation/realtime-20261118/rmuc-final/clock \
    --imu-receipts artifacts/navigation/realtime-20261118/rmuc-final/imu-receipts \
    --engine-build artifacts/navigation/realtime-20261118/engine-build-final \
    --bridge-build artifacts/navigation/realtime-20261118/bridge-build-final \
    --pointlio-build artifacts/navigation/realtime-20261118/lio-build-final \
    --offline artifacts/navigation/realtime-20261118/offline-final \
    --compatibility artifacts/navigation/realtime-20261118/compatibility-final \
    --oracle artifacts/navigation/realtime-20261118/oracle-final/verification.json \
    --gui-session artifacts/navigation/realtime-20261118/gui-final \
    --gui artifacts/navigation/realtime-20261118/gui-final/single \
    --gui artifacts/navigation/realtime-20261118/gui-final/waypoints \
    --gui artifacts/navigation/realtime-20261118/gui-final/cancel \
    --gui artifacts/navigation/realtime-20261118/gui-final/render \
    --output artifacts/navigation/realtime-20261118/release-summary.json

GUI action reports retain their existing single/waypoints/cancel format. A
separate mode=render report records actual visible-frame observations, with
checks and runtime_provenance_sha256 binding it to --gui-session.
"""
from __future__ import annotations
import argparse
import datetime
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import subprocess
import sys

from runtime_identity import capture_pointlio_inputs
from platform_cpu_policy import select_performance_cpu_sets, worker_budget_digest

ROOT = Path(__file__).resolve().parents[2]
WAVE_CHECKS = {
    "native_stack_ready", "single_goal_accepted", "single_goal_actual_arrival",
    "original_planner_path", "waypoint_sequence_accepted", "waypoint_sequence_actual_arrival",
    "waypoints_observed_in_order", "cancel_test_goal_accepted", "native_action_cancelled",
    "cancelled_vehicle_stopped", "gimbal_follows_original_shaped_target",
    "original_gimbal_rate_limit", "mounted_lidar_and_imu_live", "session_and_no_fire_preserved",
}
SPEED_CHECKS = {
    "observer_matches_the_requested_session", "fresh_import_lab_native_stack",
    "source_speed_and_smoother_selected", "synchronized_drive_telemetry_available",
    "original_nav2_goal_accepted", "actual_speed_sustained_at_source_threshold",
    "original_planner_generated_route", "cancel_request_accepted",
    "original_nav2_action_cancelled", "high_speed_cancel_respects_source_derived_bounds",
    "cancelled_vehicle_remains_stationary", "source_command_reaches_wheel_target_without_second_shaper",
    "source_command_cap_preserved", "mounted_imu_and_lidar_continued_sampling",
    "session_and_command_acceptance_preserved",
}
CLOCK_CHECKS = {
    "producer_monotonic_telemetry_present", "every_decoded_snapshot_has_clock_metadata",
    "production_measurement_contract", "session_identity_constant", "sim_clock_never_regresses",
    "producer_clock_never_regresses", "ready_and_fresh_for_entire_window",
    "complete_requested_window", "overall_rate_0p98_to_1p02",
    "all_ten_second_windows_0p98_to_1p02", "all_rolling_ten_second_windows_0p98_to_1p02",
    "native_scene_draw_telemetry_present",
    "native_scene_draw_counter_never_regresses", "native_renderer_enabled_with_60hz_target",
    "native_scene_draw_rate_60hz", "all_ten_second_scene_draw_windows_60hz",
    "all_rolling_ten_second_scene_draw_windows_60hz",
    "imu_sampling_rate_mid360_imu", "imu_no_loss_or_reset_mid360_imu",
    "lidar_ray_budget_preserved", "lidar_scan_cadence_preserved", "lidar_no_loss_or_reset",
}
FULL_CLOCK_CHECKS = CLOCK_CHECKS | {
    "imu_counter_schema_imu", "imu_counter_schema_mid360_imu", "imu_no_loss_or_reset_imu",
    "imu_sampling_rate_imu", "imu_sensor_set_present_and_constant", "lidar_counter_schema",
    "no_observation_gap_over_one_second", "no_producer_gap_over_one_second",
    "production_visual_configuration", "sensor_counter_span_positive",
}
RENDER_CHECKS = {
    "visible_ue_scene_and_vehicle", "visible_rviz_map_and_live_scan",
    "visible_scene_updates_during_navigation", "no_blank_frames_in_observed_sequence",
}
PHYSICS_OVERRIDE = "-ini:Engine:[/Script/Engine.PhysicsSettings]:MaxSubstepDeltaTime=0.005"
IMU_RECEIPT_CHECKS = {
    "native_navigation_action_gate_passed", "native_subscription_announces_selected_depth",
    "native_parameters_use_selected_depth", "cached_source_owns_selected_depth",
    "source_identity_matches_console", "native_sensor_session_matches_action",
    "native_nominal_period_matches_actual_sensor_contract", "pointlio_runtime_identity_recorded",
    "pointlio_source_manifest_self_consistent", "recorded_executable_still_matches",
    "recorded_base_config_still_matches", "recorded_native_patch_still_matches",
    "recorded_pointlio_sources_still_match", "receipt_counter_epoch_is_monotonic",
    "native_counter_records_enclose_entire_action_window", "action_window_has_no_native_header_gaps",
    "action_window_has_no_duplicates_or_backwards",
    "action_window_receives_and_accepts_every_nominal_sample",
    "action_window_header_intervals_match_sensor_period",
}
IMU_RECEIPT_COUNTERS = ("received", "accepted", "inferred_missing", "header_gaps", "duplicates", "backwards")
GUI_CHECKS = {
    "single": {"rviz_action_reaches_original_nav2", "original_nav2_goal_success",
               "actual_vehicle_reaches_selected_point", "real_planner_path_visible"},
    "waypoints": {"rviz_action_reaches_original_nav2", "original_nav2_goal_success",
                  "actual_vehicle_reaches_selected_point", "real_planner_path_visible",
                  "both_waypoints_executed_in_order", "physical_outbound_leg_observed"},
    "cancel": {"rviz_action_reaches_original_nav2", "rviz_cancel_returns_cancelled",
               "cancelled_vehicle_stays_stopped"},
}
IDENTITY_FILES = {
    "drive_profile_sha256": "assets/vehicles/aim_sentry/drive-profile.json",
    "rig_sha256": "assets/vehicles/aim_sentry/ue_mesh_rig.json",
    "lidar_sha256": "assets/vehicles/aim_sentry/sensors/mid360.json",
    "imu_sha256": "assets/vehicles/aim_sentry/sensors/mid360-imu.json",
    "navigation_follow_sha256": "assets/vehicles/aim_sentry/navigation-follow.json",
    "console_source_sha256": "tools/integration/navigation_console.py",
    "console_config_source_sha256": "tools/integration/navigation_console_config.py",
    "dependency_lock_sha256": "tools/environment/pixi.lock",
    "engine_config_sha256": "unreal/RMSim/Config/DefaultEngine.ini",
    "runtime_identity_source_sha256": "tools/integration/runtime_identity.py",
    "cpu_policy_source_sha256": "tools/integration/platform_cpu_policy.py",
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def project_path(path):
    path = Path(str(path).replace("\\", "/"))
    return path if path.is_absolute() else ROOT/path


def report_path(path):
    path = project_path(path)
    return path/"verification.json" if path.is_dir() else path


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def all_checks(data, names=None):
    checks = data.get("checks", [])
    return (data.get("status") == "passed" and isinstance(checks, list) and bool(checks)
            and all(isinstance(c, dict) and c.get("passed") is True for c in checks)
            and (not names or names <= {c.get("name") for c in checks}))


def check_named(data, name):
    return next((c for c in data.get("checks", []) if c.get("name") == name), {})




STAGING_CHECKS = {
    "staging_original_nav2_goal_accepted", "staging_original_nav2_action_succeeded",
    "staging_actual_position_within_original_25cm", "staging_vehicle_stationary_before_formal_goal",
    "staged_lane_has_source_speed_and_collision_clearance",
}


def localization_map_checks(provenance, declared_map, helper):
    """Validate the selected map and current package without exporting or rebuilding."""
    checks, details = {}, {}
    scene = provenance.get("scene") or {}
    selection = scene.get("localization_map_selection")
    actual = provenance.get("localization_map")
    packaged = scene.get("localization_map_package")
    selected = scene.get("localization_map_yaml")
    expected = project_path(declared_map).resolve() if declared_map and scene.get("scene") == "rmuc2027" else None
    details["selection"] = selection
    try:
        selection_valid = selection in ("package_default", "explicit", "single_map")
        if selection == "package_default" or (selection == "single_map" and scene.get("scene") == "rmuc2027"):
            manifest = ROOT/"assets/maps/rmuc2027/navigation-console/SOURCE.json"
            package = helper.packaged_localization_map(
                manifest.parent, json.loads(manifest.read_text(encoding="utf-8-sig")))
            details["package_manifest"] = {"path": str(manifest), "sha256": digest(manifest)}
            if selection == "package_default":
                selected_package_path = project_path(package["yaml_path"]).resolve() if package else None
                selection_valid = (selection_valid and scene.get("scene") == "rmuc2027"
                    and package is not None and package.get("missing_derived") == []
                    and (expected is None or expected == selected_package_path)
                    and isinstance(packaged, dict)
                    and project_path(packaged.get("manifest_path", "")).resolve() == manifest.resolve()
                    and packaged.get("manifest_sha256") == digest(manifest))
                expected = selected_package_path
                details["package_files"] = package.get("files") if package else None
            else:
                selection_valid = selection_valid and package is None
        elif selection == "explicit":
            # An explicitly selected map is independent of the unused package default.
            selection_valid = selection_valid and expected is not None and packaged is None
        if selection == "single_map":
            selection_valid = selection_valid and not selected and packaged is None and expected is None
            map_matches = actual is None
        else:
            selection_valid = (selection_valid and bool(selected) and expected is not None
                               and project_path(selected).resolve() == expected)
            map_matches = (isinstance(actual, dict) and expected is not None
                           and actual == helper.validate_localization_map(scene["map_yaml"], expected))
        checks["localization_map_selection_and_current_package_match"] = bool(selection_valid)
        checks["matches_declared_navigation_and_localization_maps"] = bool(map_matches)
        details["expected_localization_map_yaml"] = str(expected) if expected else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        checks["localization_map_selection_and_current_package_match"] = False
        checks["matches_declared_navigation_and_localization_maps"] = False
        details["error"] = repr(error)
    return checks, details


def top_speed_phase_checks(report, trace, identity):
    """Recompute formal statistics from its raw phase, excluding the staging action."""
    checks, metrics = {}, {}
    close = lambda a, b: finite(a) and finite(b) and abs(a-b) <= 1.e-8
    try:
        contract = report["contract"]
        formal = report["formal_measurement"]
        staging = report["staging"]
        actions = report["actions"]
        staged, action = actions
        start, end = formal["start_stamp"], formal["end_stamp"]
        start_wall, end_wall = formal["start_wall"], formal["end_wall"]
        checks["top_speed_two_distinct_original_actions_and_formal_interval"] = (
            report.get("schema_version") == trace.get("schema_version") == contract.get("fixture_version") == 2
            and trace.get("actions") == actions and bool(identity)
            and staged.get("phase") == "staging" and action.get("phase") == "formal"
            and staged.get("goal_position_m") == [-8.5, 0., 0.]
            and action.get("goal_position_m") == [8.5, 0., 0.]
            and all(a.get("session_identity") == identity and a.get("accepted") is True
                    and a.get("frame_id") == "aim_sentry/map"
                    and re.fullmatch(r"[0-9a-f]{32}", a.get("goal_id_hex", "")) for a in actions)
            and staged["goal_id_hex"] != action["goal_id_hex"]
            and staged.get("result_status") == 4 and action.get("result_status") == 5
            and formal.get("measurement_phase") == "formal" and formal.get("session_identity") == identity
            and formal.get("goal_id_hex") == action["goal_id_hex"]
            and all(finite(v) for v in (start, end, start_wall, end_wall))
            and staged["requested_stamp"] <= staged["accepted_stamp"] <= staged["result_stamp"]
                <= staging["stopped_start_stamp"] <= staging["stopped_confirmed_stamp"]
                <= start <= action["requested_stamp"] <= action["accepted_stamp"] <= action["result_stamp"] <= end
            and staged["requested_wall"] <= staged["accepted_wall"] <= staged["result_wall"]
                <= start_wall <= action["requested_wall"] <= action["accepted_wall"] <= action["result_wall"] <= end_wall
            and start < end and start_wall < end_wall)
        actual_start = formal["actual_start_position_m"]
        stage_error = math.dist(staging["actual_position_m"][:2], [-8.5, 0.])
        stage_seconds = staging["stopped_confirmed_stamp"] - staging["stopped_start_stamp"]
        stage_rows = [r for r in trace["truth"] if r.get("phase") == "staging"
                      and staging["stopped_start_stamp"] <= r["stamp"] <= staging["stopped_confirmed_stamp"]]
        checks["top_speed_actual_staging_arrival_and_full_stationary_window"] = (
            staging.get("session_identity") == identity and staging.get("goal_id_hex") == staged["goal_id_hex"]
            and staging.get("action_status") == 4 and staging.get("action_succeeded") is True
            and staging.get("goal_m") == contract.get("staging_goal") == contract.get("start") == [-8.5, 0.]
            and contract.get("spawn_start") == [0., 0.] and contract.get("goal") == [8.5, 0.]
            and staging.get("position_tolerance_m") == .25 and stage_error <= .25
            and close(staging.get("actual_error_m"), stage_error)
            and staging.get("stop_speed_limit_mps") == .05 and finite(staging.get("speed_mps"))
            and staging["speed_mps"] < .05 and staging.get("required_stationary_seconds") == .5
            and stage_seconds >= .5 and close(staging.get("stationary_seconds"), stage_seconds)
            and staging.get("within_position_tolerance") is True and staging.get("stopped") is True
            and actual_start == staging["actual_position_m"] and bool(stage_rows)
            and close(stage_rows[0]["stamp"], staging["stopped_start_stamp"])
            and close(stage_rows[-1]["stamp"], staging["stopped_confirmed_stamp"])
            and stage_rows[-1]["position"] == actual_start
            and all(r.get("session_identity") == identity and finite(r.get("speed_mps"))
                    and r["speed_mps"] < .05 for r in stage_rows)
            and all(0 < b["stamp"]-a["stamp"] <= .15 for a, b in zip(stage_rows, stage_rows[1:])))
        rows = {name: [r for r in trace[name] if r.get("phase") == "formal" and r["stamp"] >= start]
                for name in ("truth", "drive", "commands", "plans")}
        truth, drives, commands = rows["truth"], rows["drive"], rows["commands"]
        # Report uses singular command_samples; raw topic arrays retain their existing names.
        checks["top_speed_formal_samples_exclude_staging_and_keep_session_identity"] = (
            all(rows[name] for name in rows)
            and all(r.get("session_identity") == identity for values in rows.values() for r in values)
            and formal.get("truth_samples") == len(truth) and formal.get("drive_samples") == len(drives)
            and formal.get("command_samples") == len(commands)
            and all(0 < b["stamp"]-a["stamp"] for a, b in zip(truth, truth[1:]))
            and close(truth[-1]["stamp"], end) and all(r["wall"] >= start_wall for r in truth)
            and any(r.get("points", 0) > 2 for r in rows["plans"]))
        # Reconstruct speeds from formal positions only, using the observer's declared
        # 50..100 ms chord window. Clearing this deque excludes all pre-goal motion.
        from collections import deque
        recent, recomputed, speed_errors = deque(), [], []
        previous = None
        for row in truth:
            if previous is not None and row["stamp"]-previous > .15:
                recent.clear()
            previous = row["stamp"]
            recent.append(row)
            while len(recent) > 2 and row["stamp"]-recent[1]["stamp"] > .1:
                recent.popleft()
            dt = row["stamp"]-recent[0]["stamp"]
            if dt >= .05:
                value = math.dist(recent[0]["position"][:2], row["position"][:2])/dt
                recomputed.append((row["stamp"], value))
                speed_errors.append(abs(value-row["speed_mps"]))
        peak = max((value for stamp, value in recomputed), default=None)
        command_peak = max(math.hypot(*r["linear"]) for r in commands)
        checks["top_speed_peak_and_sustained_speed_reproduced_only_from_formal_truth"] = (
            bool(recomputed) and max(speed_errors) <= 1.e-8
            and close(peak, report.get("actual_peak_speed_mps"))
            and close(peak, formal.get("actual_peak_speed_mps")))
        sustained = formal["sustained"]
        interval = [(stamp, value) for stamp, value in recomputed
                    if sustained["start_stamp"] <= stamp <= sustained["confirmed_stamp"]]
        checks["top_speed_raw_formal_interval_sustains_original_source_threshold"] = (
            sustained.get("threshold_mps") == contract.get("speed_threshold_mps")
            and sustained.get("required_duration_s") == contract.get("sustained_seconds") == .3
            and start <= sustained["start_stamp"] < sustained["confirmed_stamp"] <= end
            and sustained["confirmed_stamp"]-sustained["start_stamp"] >= .3
            and bool(interval) and close(interval[0][0], sustained["start_stamp"])
            and close(interval[-1][0], sustained["confirmed_stamp"])
            and all(value >= contract["speed_threshold_mps"] for stamp, value in interval)
            and all(0 < b[0]-a[0] <= .15 for a, b in zip(interval, interval[1:])))
        command_check = check_named(report, "source_command_cap_preserved")
        checks["top_speed_formal_command_peak_preserves_source_cap"] = (
            close(formal.get("command_peak_speed_mps"), command_peak)
            and close(command_check.get("observed_command_peak_mps"), command_peak)
            and command_peak <= contract["source_cap_mps"]+1.e-5)
        errors = []
        for row in drives:
            drive = row["drive"]
            desired = drive.get("applied_requested_world_linear_m_s")
            if desired is not None and math.hypot(*desired[:2]) > .2:
                errors.append((math.dist(desired[:2], drive["filtered_world_linear_m_s"][:2]),
                    abs(drive["applied_requested_yaw_rad_s"]-drive["filtered_yaw_rad_s"]),
                    drive["applied_velocity_command_mode"]))
        tracking = check_named(report, "source_command_reaches_wheel_target_without_second_shaper")
        checks["top_speed_formal_drive_targets_reproduce_unshaped_tracking"] = (
            len(errors) >= 20 and tracking.get("samples") == len(errors)
            and close(tracking.get("max_linear_error_mps"), max((r[0] for r in errors), default=None))
            and close(tracking.get("max_yaw_error_rad_s"), max((r[1] for r in errors), default=None))
            and all(linear <= 1.e-6 and yaw <= 1.e-6 and mode == "external_shaped_velocity"
                    for linear, yaw, mode in errors))
        cancellation = report["cancellation"]
        stop_rows = [r for r in truth if cancellation["stamp"] <= r["stamp"] <= cancellation["stop_confirmed_stamp"]]
        distance = sum(math.dist(a["position"][:2], b["position"][:2]) for a, b in zip(stop_rows, stop_rows[1:]))
        checks["top_speed_cancel_distance_uses_only_formal_truth"] = (
            len(stop_rows) > 1 and start <= cancellation["stamp"] < cancellation["stop_confirmed_stamp"] <= end
            and close(distance, cancellation.get("actual_stop_distance_m"))
            and distance <= cancellation["distance_bound_m"])
        metrics = {"staging_error_m": stage_error, "staging_stationary_seconds": stage_seconds,
                   "formal_truth_samples": len(truth), "formal_drive_samples": len(drives),
                   "formal_command_samples": len(commands), "formal_actual_peak_mps": peak,
                   "formal_command_peak_mps": command_peak,
                   "formal_measurement_sim_interval": [start, end],
                   "formal_measurement_wall_interval": [start_wall, end_wall]}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError) as error:
        checks["top_speed_raw_phase_schema_readable"] = False
        metrics["error"] = repr(error)
    return checks, metrics

def cpu_policy_checks(policy, provenance, session, status, helper_hash):
    """Require native readback, source binding and the actual owned process identity."""
    checks = {}
    try:
        expected_path = project_path(session)/"process-policy/verification.json"
        provenance_path = project_path(session)/"runtime-provenance.json"
        checks["cpu_policy_immutable_session_binding"] = (
            project_path(policy.get("session_directory", "")) == project_path(session)
            and project_path(policy.get("runtime_provenance_path", "")) == provenance_path
            and policy.get("runtime_provenance_sha256") == digest(provenance_path)
            and policy.get("module_sha256") == provenance.get("module_sha256")
            and project_path(provenance.get("process_policy_path", "")) == expected_path
            and status.get("process_policy", {}).get("sha256") == digest(expected_path))
        checks["cpu_policy_current_helper_and_request"] = (
            policy.get("schema_version") == 1 and policy.get("mode") == "owned_process_cpu_sets"
            and policy.get("helper_sha256") == provenance.get("cpu_policy_source_sha256") == helper_hash
            and policy.get("requested_policy") == provenance.get("cpu_policy_requested") == "performance"
            and policy.get("platform") == provenance.get("cpu_policy_platform") == "nt"
            and policy.get("paced_navigation") is True and provenance.get("pace_real_time") is True
            and policy.get("capture_phase") == "immediately_after_owned_ue_popen"
            and finite(policy.get("started_monotonic_s")) and finite(policy.get("completed_monotonic_s"))
            and policy["started_monotonic_s"] <= policy["completed_monotonic_s"])
        native = policy.get("native_process", {})
        command = policy.get("command", [])
        normalized = lambda p: str(p).replace("\\", "/").casefold()
        checks["cpu_policy_actual_owned_process"] = (
            type(policy.get("pid")) is int and policy["pid"] > 0
            and policy.get("handle_source") == "owned_subprocess_popen_handle"
            and native.get("pid") == policy["pid"] == status.get("process_policy", {}).get("pid")
            and policy.get("launcher_pid") == status.get("pid")
            and native.get("exit_code") == 259 and bool(command)
            and normalized(native.get("executable")) == normalized(policy.get("expected_executable"))
            == normalized(command[0])
            and normalized(ROOT/"unreal/RMSim/RMSim.uproject") in [normalized(x) for x in command]
            and "-simrealtimepace" in [normalized(x) for x in command]
            and "-abslog="+normalized(project_path(session)/"runtime.log") in [normalized(x) for x in command])
        before, after = policy.get("before", {}), policy.get("after", {})
        decision = select_performance_cpu_sets(policy.get("topology", []), before)
        preflight = provenance.get("worker_budget") or {}
        if preflight.get("action") == "preserve":
            decision = dict(decision, action="preserve", requested_cpu_set_ids=[],
                            reason="worker_budget_preflight_preserve:"+str(preflight.get("reason")))
        requested = decision["requested_cpu_set_ids"]
        applied = decision["action"] == "apply"
        expected = requested if applied else before.get("default_cpu_sets")
        checks["cpu_policy_dynamic_selection_and_native_readback"] = (
            policy.get("decision") == decision and policy.get("requested_cpu_set_ids") == requested
            and policy.get("observed_default_cpu_set_ids") == after.get("default_cpu_sets") == expected
            and policy.get("outcome") == ("applied" if applied else "unchanged")
            and all(after.get(key) == before.get(key) and key in before
                    for key in ("affinity", "priority_class", "power_throttling")))
        required = {"valid_requested_policy", "owned_live_popen", "owned_executable_argument_matches",
                    "borrowed_popen_handle_present", "native_process_identity_matches_owned_popen",
                    "native_cpu_sets_readback_matches_decision", "affinity_priority_and_power_policy_unchanged"}
        if applied:
            required.add("owned_process_still_live_before_apply")
        calls = policy.get("native_calls", [])
        api_names = {c.get("api") for c in calls}
        required_apis = {"GetProcessId", "GetExitCodeProcess", "QueryFullProcessImageNameW",
                         "GetProcessAffinityMask", "GetPriorityClass", "GetProcessInformation",
                         "GetProcessDefaultCpuSets", "GetSystemCpuSetInformation"}
        checks["cpu_policy_native_calls_and_checks_passed"] = (
            all_checks(policy, required) and bool(calls) and required_apis <= api_names
            and api_names <= required_apis | {"SetProcessDefaultCpuSets"}
            and sum(c.get("api") == "SetProcessDefaultCpuSets" for c in calls) == int(applied)
            and all(c.get("succeeded") is True or
                    (c.get("api") in ("GetProcessDefaultCpuSets", "GetSystemCpuSetInformation")
                     and c.get("expected_buffer_query") is True and c.get("last_error") == 122)
                    for c in calls))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        checks["cpu_policy_schema_and_evidence_readable"] = False
    return checks



def worker_budget_checks(policy, provenance, runtime_text):
    """Bind prelaunch topology, actual Popen arguments, child API and UE startup cores."""
    checks = {}
    try:
        pre = provenance["worker_budget"]
        child = policy["worker_budget"]
        fingerprint = worker_budget_digest(pre)
        checks["worker_budget_source_hash_command_and_time_binding"] = (
            provenance.get("worker_budget_sha256") == child.get("preflight_sha256") == fingerprint
            and pre.get("helper_sha256") == provenance.get("cpu_policy_source_sha256") == policy.get("helper_sha256")
            and pre.get("requested_policy") == policy.get("requested_policy") == "performance"
            and pre.get("platform") == policy.get("platform") == "nt"
            and pre.get("paced_navigation") is True and pre.get("launch_allowed") is True
            and pre.get("launcher_pid") == policy.get("launcher_pid") == pre.get("parent_process", {}).get("pid")
            and pre.get("parent_process", {}).get("exit_code") == 259
            and pre.get("command") == provenance.get("ue_command") == policy.get("command")
            and worker_budget_digest(pre["command"]) == pre.get("command_sha256")
            == provenance.get("ue_command_sha256") == child.get("actual_ue_command_sha256")
            and finite(pre.get("started_monotonic_s")) and finite(pre.get("completed_monotonic_s"))
            and pre["started_monotonic_s"] <= pre["completed_monotonic_s"] <= policy["started_monotonic_s"])
        parent_decision = select_performance_cpu_sets(pre["topology"], pre["parent_before"])
        raw_child = select_performance_cpu_sets(policy["topology"], policy["before"])
        parent_ids = parent_decision["requested_cpu_set_ids"]
        parent_rows = [row for row in pre["topology"] if row["id"] in parent_ids]
        parent_allocated = any(row["flags"] & 2 for row in parent_rows)
        expected_action = "apply" if parent_decision["action"] == "apply" and not parent_allocated else "preserve"
        expected_reason = "parent_allocated_cpu_sets_cannot_predict_child" if parent_allocated else parent_decision["reason"]
        checks["worker_budget_parent_and_child_native_decisions_match"] = (
            pre.get("decision") == parent_decision and child.get("child_dynamic_decision") == raw_child
            and pre.get("action") == child.get("declared_action") == expected_action
            and pre.get("reason") == expected_reason)
        apis = pre.get("native_calls", [])
        required_apis = {"GetCurrentProcess", "GetProcessId", "GetExitCodeProcess", "QueryFullProcessImageNameW",
                         "GetProcessAffinityMask", "GetPriorityClass", "GetProcessInformation",
                         "GetProcessDefaultCpuSets", "GetSystemCpuSetInformation"}
        checks["worker_budget_preflight_is_successful_and_read_only"] = (
            all_checks(pre, {"valid_requested_policy", "explicit_ue_argument_vector", "readonly_current_parent_identity"})
            and {row.get("api") for row in apis} == required_apis
            and all(row.get("succeeded") is True or
                    (row.get("api") in ("GetProcessDefaultCpuSets", "GetSystemCpuSetInformation")
                     and row.get("expected_buffer_query") is True and row.get("last_error") == 122) for row in apis))
        required_child_checks = {"worker_budget_preflight_passed", "worker_budget_request_and_source_match",
                                 "worker_budget_actual_command_matches_declared"}
        observed_cores = [int(value) for value in re.findall(r"\bLogInit:\s+CPU Page size=\d+,\s+Cores=(\d+)\b", runtime_text)]
        if expected_action == "apply":
            if any(type(row.get("core")) is not int or not 0 <= row["core"] <= 255 for row in parent_rows):
                raise ValueError("Invalid native physical core index")
            physical = len({(row["group"], row["core"]) for row in parent_rows})
            actual_rows = [row for row in policy["topology"] if row["id"] in parent_ids]
            physical_actual = len({(row["group"], row["core"]) for row in actual_rows})
            limits = []
            for arg in pre["command"]:
                if re.match(r"^-+(?:physical)?corelimit", arg, re.I):
                    match = re.fullmatch(r"-(physicalcorelimit|corelimit)=(\d+)", arg, re.I)
                    if match is None:raise ValueError("Ambiguous actual UE core limit")
                    limits.append((match[1].casefold(), int(match[2]), arg))
            core_arguments = [arg for name, value, arg in limits if name == "corelimit"]
            exact_arguments = len({name for name,value,arg in limits}) == len(limits) and all(value == physical for name,value,arg in limits)
            if pre.get("corelimit_source") == "automatic":
                command_preserved = pre["command"] == [*pre["command_before"], pre["corelimit_argument"]]
            else:
                command_preserved = pre.get("corelimit_source") == "existing_matching" and pre["command"] == pre["command_before"]
            checks["worker_budget_exact_dynamic_core_counts_and_arguments"] = (
                physical > 0 and physical == physical_actual == pre.get("physical_core_count")
                == child.get("expected_physical_core_count") == child.get("actual_physical_core_count")
                == pre.get("corelimit") == child.get("corelimit") == provenance.get("ue_corelimit")
                and len(parent_ids) == len(actual_rows) == pre.get("logical_processor_count")
                == child.get("expected_logical_processor_count") == child.get("actual_logical_processor_count")
                and pre.get("expected_cpu_set_ids") == child.get("expected_cpu_set_ids")
                == raw_child["requested_cpu_set_ids"] == parent_ids
                and pre.get("expected_windows_worker_threads_to_spawn") == max(physical-1,2)
                and pre.get("outcome") == "limited" and exact_arguments and command_preserved
                and core_arguments == [pre.get("corelimit_argument")] and child.get("corelimit_argument") == core_arguments[0])
            checks["worker_budget_native_ue_startup_core_count_matches"] = observed_cores == [physical]
            required_child_checks |= {"worker_budget_actual_cpu_sets_match_preflight",
                                      "worker_budget_actual_core_counts_match_preflight",
                                      "worker_budget_corelimit_present_before_ue_startup"}
        else:
            checks["worker_budget_preserves_existing_process_restrictions"] = (
                pre.get("corelimit") is None and child.get("corelimit") is None
                and provenance.get("ue_corelimit") is None and pre.get("expected_cpu_set_ids") == []
                and pre.get("command") == pre.get("command_before") and pre.get("outcome") == "unchanged")
            checks["worker_budget_native_ue_startup_recorded"] = len(observed_cores) == 1 and observed_cores[0] > 0
            required_child_checks.add("worker_budget_preserve_declaration")
        checks["worker_budget_actual_owned_child_checks_passed"] = all_checks(policy, required_child_checks)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        checks["worker_budget_complete_evidence_readable"] = False
    return checks


def action_trace_interval(trace, identity):
    """Use all raw action receipt rows; never infer timing from report file dates."""
    rows = trace.get("performance")
    interval = {"valid": False, "samples": len(rows) if isinstance(rows, list) else 0,
                "session_identity": identity}
    if (not isinstance(rows, list) or len(rows) < 2
            or not all(isinstance(row, dict)
                       and finite(row.get("received_monotonic_s"))
                       and finite(row.get("sim_time_ns")) for row in rows)):
        return interval
    clocks = [row["received_monotonic_s"] for row in rows]
    stamps = [row["sim_time_ns"] for row in rows]
    interval.update(start_monotonic_s=clocks[0], end_monotonic_s=clocks[-1],
                    start_sim_time_s=stamps[0]*1.e-9, end_sim_time_s=stamps[-1]*1.e-9,
                    phases=sorted({str(row.get("probe_phase")) for row in rows}))
    interval["valid"] = bool(identity and len(identity) == 2
        and all(item is not None for item in identity)
        and all([row.get("session"), row.get("epoch")] == list(identity) for row in rows)
        and clocks[-1] > clocks[0] and stamps[-1] > stamps[0]
        and all(b >= a for a, b in zip(clocks, clocks[1:]))
        and all(b >= a for a, b in zip(stamps, stamps[1:])))
    return interval


def clock_covers_action(clock, interval):
    """Require the production clock's complete wall/simulation window to contain action evidence."""
    clock_fields = ("window_start_monotonic_s", "window_end_monotonic_s",
                    "window_start_sim_time_s", "window_end_sim_time_s")
    if (interval.get("valid") is not True or clock.get("measurement_complete") is not True
            or not all(finite(clock.get(key)) for key in clock_fields)):
        return False
    clock_identity = clock.get("session_identity")
    if not isinstance(clock_identity, (list, tuple)):
        return False
    return (list(clock_identity) == list(interval.get("session_identity") or [])
            and clock["window_start_monotonic_s"] <= interval["start_monotonic_s"]
            and clock["window_end_monotonic_s"] >= interval["end_monotonic_s"]
            and clock["window_start_sim_time_s"] <= interval["start_sim_time_s"]
            and clock["window_end_sim_time_s"] >= interval["end_sim_time_s"])


def receipt_counter_checks(data, action, native_log):
    """Independently recompute the reported receipt interval from the bound native log."""
    checks = []
    def add(name, passed, **detail):
        checks.append({"name": name, "passed": bool(passed), **detail})
    rows = []
    for number, line in enumerate(native_log.splitlines(), 1):
        if "POINTLIO_IMU_RX " in line:
            values = {key: float(value) for key, value in
                      re.findall(r"(\w+)=([-+\d.eE]+)", line.split("POINTLIO_IMU_RX ", 1)[1])}
            rows.append({"line": number, **values})
    fields = (*IMU_RECEIPT_COUNTERS, "raw_stamp_s", "first_stamp_s",
              "nominal_period_s", "window_max_header_dt_s")
    valid = len(rows) >= 2 and all(all(finite(row.get(key)) for key in fields) for row in rows)
    add("raw_native_receipt_counter_schema", valid)
    if not valid:
        return checks
    interval = data.get("enclosing_counter_interval") or {}
    first = next((index for index, row in enumerate(rows)
                  if row["line"] == interval.get("first_log_line")), None)
    last = next((index for index, row in enumerate(rows)
                 if row["line"] == interval.get("last_log_line")), None)
    endpoints = first is not None and last is not None and first < last
    add("raw_native_receipt_interval_endpoints_present", endpoints)
    if not endpoints:
        return checks
    selected = rows[first:last+1]
    left, right = selected[0], selected[-1]
    span = [left["raw_stamp_s"], right["raw_stamp_s"]]
    period = .005
    add("raw_native_receipt_epoch_monotonic",
        all(all(b[key] >= a[key] for key in (*IMU_RECEIPT_COUNTERS, "raw_stamp_s"))
            for a, b in zip(selected, selected[1:]))
        and all(row["first_stamp_s"] == left["first_stamp_s"] for row in selected)
        and all(row[key] >= 0 and row[key].is_integer() for row in selected for key in IMU_RECEIPT_COUNTERS))
    add("raw_native_receipt_window_covers_full_action",
        action.get("valid") is True and span[0] <= action["start_sim_time_s"]
        and span[1] >= action["end_sim_time_s"] and span[1] > span[0],
        native_sim_window_s=span, action_sim_window_s=[action.get("start_sim_time_s"), action.get("end_sim_time_s")])
    delta = {key: int(right[key]-left[key]) for key in IMU_RECEIPT_COUNTERS}
    expected = int(round((span[1]-span[0])/period))
    maximum = max(row["window_max_header_dt_s"] for row in selected[1:])
    add("raw_native_receipt_interval_matches_report",
        interval.get("sim_window_s") == span and interval.get("counter_delta") == delta
        and interval.get("expected_samples") == expected
        and interval.get("max_header_interval_s") == maximum)
    add("raw_native_receipt_no_gaps_duplicates_or_backwards",
        all(delta[key] == 0 for key in IMU_RECEIPT_COUNTERS[2:]), counter_delta=delta)
    add("raw_native_receipt_all_nominal_samples_received_and_accepted",
        expected > 0 and delta["received"] == delta["accepted"] == expected, expected_samples=expected)
    add("raw_native_receipt_preserves_200hz_header_period",
        all(abs(row["nominal_period_s"]-period) <= 1.e-9 for row in selected)
        and 0 < maximum <= period+1.e-6, maximum_header_interval_s=maximum)
    return checks


def compatibility_case_command_matches(name, command, base):
    """Match the actual three wrapper commands and their physical fixture options."""
    scripts = {"sentry": "Test-SentryConsoleRegression.ps1",
               "manual-waves": "Test-GyroControls.ps1",
               "suspension": "Test-Suspension.ps1"}
    if name not in scripts or not isinstance(command, list) or not all(isinstance(x, str) for x in command):
        return False
    if len(command) < 8 or Path(command[0]).name.lower() not in {"powershell", "powershell.exe"}:
        return False
    if ([x.lower() for x in (command[1], command[2], command[4], command[6])] !=
            ["-noprofile", "-file", "-engineroot", "-evidencedirectory"]):
        return False
    if (project_path(command[3]) != ROOT/"tools/unreal"/scripts[name]
            or project_path(command[7]) != base/name or not command[5].strip()):
        return False
    tail = command[8:]
    if name == "sentry":
        return tail == []
    try:
        if name == "manual-waves":
            return (len(tail) == 6
                    and [tail[i].lower() for i in (0, 1, 2, 4)] ==
                        ["-waveroutes", "-precisionkeys", "-spinrads", "-timeoutseconds"]
                    and float(tail[3]) == 4. and float(tail[5]) > 0)
        return (len(tail) == 4
                and [tail[i].lower() for i in (0, 2)] == ["-stepseconds", "-timeoutseconds"]
                and float(tail[1]) == .005 and float(tail[3]) > 0)
    except (TypeError, ValueError):
        return False


def compatibility_suspension_checks(directory, current, read_report):
    """Verify Test-Suspension.ps1's four native checks and unchanged trace analysis."""
    checks = []
    def add(name, passed, **detail):
        checks.append({"name": name, "passed": bool(passed), **detail})
    def read(name):
        return read_report(directory/name, "compatibility_suspension_"+name.replace(".json", ""))
    report = read("verification.json")
    native = read("control-result.json")
    provenance = read("runtime-provenance.json")
    items = native.get("checks") if isinstance(native.get("checks"), list) else []
    names = {row.get("name") for row in items if isinstance(row, dict)}
    required = {"suspension_baseline_settled", "suspension_loaded_equilibrium",
                "suspension_release_recovered", "suspension_impulse_recovered"}
    add("compatibility_suspension_four_native_checks_passed",
        all_checks(native, required) and native.get("complete") is True
        and len(items) == 4 and names == required and bool(native.get("run_id")),
        expected_checks=4, observed_checks=len(items), check_names=sorted(str(x) for x in names))
    hashes = provenance.get("hashes") or {}
    add("compatibility_suspension_inputs_and_fixture_match",
        report.get("provenance") == provenance
        and all(str(hashes.get(key, "")).lower() == current[target]
                for key, target in (("module", "module_sha256"), ("profile", "drive_profile_sha256"),
                                    ("rig", "rig_sha256")))
        and str(hashes.get("map", "")).lower() == digest(ROOT/"assets/maps/import_lab/map.json")
        and provenance.get("fixed_step_s") == .005 and provenance.get("load_n") == 100
        and native.get("external_load_n") == 100
        and native.get("physics_parameters", {}).get("SpringStiffnessNPerM") == 2000
        and native.get("physics_parameters", {}).get("SpringDampingNsPerM") == 140)
    add("compatibility_suspension_analysis_and_capture_counts_passed",
        report.get("status") == "passed" and report.get("analysis_failure") is None
        and report.get("native_checks") == 4 and report.get("decoded_pngs") == 6
        and report.get("process_exit") == 0
        and report.get("samples") == len(native.get("samples", [])) > 0)
    try:
        from analyze_suspension import analyze
        recomputed = analyze(directory)
        metrics = report.get("metrics") or {}
        actual_metrics = recomputed["metrics"]
        metrics_equal = (set(metrics) == set(actual_metrics)
                         and all(metrics[key] == value if value is None else
                                 finite(metrics[key]) and math.isclose(metrics[key], value,
                                                                     rel_tol=1.e-12, abs_tol=1.e-10)
                                 for key, value in actual_metrics.items()))
        add("compatibility_suspension_original_trace_analysis_reproduced",
            recomputed["status"] == "passed"
            and report.get("raw_report_sha256") == recomputed["raw_report_sha256"]
            and report.get("physics_parameters") == recomputed["physics_parameters"]
            and metrics_equal,
            analyzer_sha256=digest(ROOT/"tools/integration/analyze_suspension.py"))
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError) as error:
        add("compatibility_suspension_original_trace_analysis_reproduced", False, error=repr(error))
    leaf = {"group": "suspension", "path": "suspension", "expected_checks": 4,
            "observed_checks": len(items),
            "passed_checks": sum(isinstance(row, dict) and row.get("passed") is True for row in items),
            "passed": all(row["passed"] for row in checks)}
    return checks, leaf, native.get("run_id")


def compatibility_checks(directory, current, pointlio, read_report):
    """Require 70 sentry, ten manual wave and four suspension native checks."""
    base = project_path(directory)
    checks, leaves = [], []
    def add(name, passed, **detail):
        checks.append({"name": name, "passed": bool(passed), **detail})
    def read(relative):
        return read_report(base/relative, "compatibility_"+relative.replace("/", "_").replace(".json", ""))
    expected_inputs = {
        "unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll": current["module_sha256"],
        "unreal/RMSim/Config/DefaultEngine.ini": current["engine_config_sha256"],
        "assets/vehicles/aim_sentry/drive-profile.json": current["drive_profile_sha256"],
        "assets/vehicles/aim_sentry/ue_mesh_rig.json": current["rig_sha256"],
        "ros2_ws/install_lio/point_lio/lib/point_lio/pointlio_mapping.exe": pointlio["executable_sha256"],
    }
    wrapper = read("verification.json")
    frozen, final = wrapper.get("frozen_inputs"), wrapper.get("final_inputs")
    add("compatibility_frozen_and_final_inputs_match_current", frozen == final == expected_inputs)
    cases = wrapper.get("cases") or []
    add("compatibility_three_original_case_groups_passed",
        wrapper.get("status") == "passed" and len(cases) == 3
        and {row.get("name") for row in cases} == {"sentry", "manual-waves", "suspension"}
        and all(row.get("exit_code") == 0 and row.get("inputs_unchanged") is True for row in cases),
        observed_cases=[{"name": row.get("name"), "exit_code": row.get("exit_code"),
                         "inputs_unchanged": row.get("inputs_unchanged")} for row in cases])
    for name in ("sentry", "manual-waves", "suspension"):
        selected = [row for row in cases if row.get("name") == name]
        add("compatibility_"+name.replace("-", "_")+"_original_command_matches",
            len(selected) == 1 and compatibility_case_command_matches(name, selected[0].get("command"), base))
    sentry = read("sentry/verification.json")
    runs = sentry.get("runs") or []
    add("compatibility_sentry_five_original_cases_passed",
        sentry.get("status") == "passed" and len(runs) == 5
        and {row.get("name") for row in runs} == {"mid360", "manual", "ros", "aim", "lidar-lk"}
        and all(row.get("passed") is True and row.get("process_exit") == 0 for row in runs))
    for relative, wave in (("sentry/manual", False), ("manual-waves", True)):
        data = read(relative+"/verification.json")
        add("compatibility_"+relative.replace("/", "_")+"_route_selection_passed",
            data.get("passed") is True and data.get("wave_routes") is wave
            and data.get("spawns") == (["wave_red", "wave_blue"] if wave else ["default"])
            and (not wave or data.get("precision_keys") is True and data.get("spin_rad_s") == 4))

    # Counts follow the actual scripts named by the native wrapper. A failed or
    # truncated native control-result cannot be replaced with a wrapper flag.
    control_leaves = [
        ("sentry", "sentry/manual/default", 19, 12),
        ("manual-waves", "manual-waves/wave_red", 5, 6),
        ("manual-waves", "manual-waves/wave_blue", 5, 6),
    ]
    simple_leaves = [
        ("sentry", "sentry/mid360/tcp", 9),
        ("sentry", "sentry/ros/ros", 11),
        ("sentry", "sentry/aim/aim", 20),
        ("sentry", "sentry/lidar-lk/ros", 11),
    ]
    wave_names = {"gyro_supported_spawn", "gyro_spin_up", "gyro_forward_wave_route",
                  "gyro_route_brake", "gyro_reverse_wave_route"}
    native_run_ids = []
    for group, relative, expected, captures in control_leaves:
        wrapper = read(relative+"/verification.json")
        native = read(relative+"/control-result.json")
        items = native.get("checks") if isinstance(native.get("checks"), list) else []
        names = {row.get("name") for row in items if isinstance(row, dict)}
        passed = (all_checks(native) and native.get("complete") is True
                  and len(items) == expected and len(names) == expected
                  and wrapper.get("passed") is True and wrapper.get("process_exit") == 0
                  and wrapper.get("checks") == expected and wrapper.get("decoded_captures") == captures
                  and bool(native.get("run_id")) and wrapper.get("run_id") == native.get("run_id")
                  and all(str(wrapper.get(key, "")).lower() == current[key]
                          for key in ("module_sha256", "drive_profile_sha256", "rig_sha256")))
        if relative.endswith(("wave_red", "wave_blue")):
            passed = passed and names == wave_names
        add("compatibility_"+relative.replace("/", "_")+"_complete_native_control_passed", passed,
            expected_checks=expected, observed_checks=len(items), check_names=sorted(str(x) for x in names))
        if native.get("run_id"):
            native_run_ids.append(native["run_id"])
        leaves.append({"group": group, "path": relative, "expected_checks": expected,
                       "observed_checks": len(items),
                       "passed_checks": sum(isinstance(row, dict) and row.get("passed") is True for row in items),
                       "passed": passed})
    for group, relative, expected in simple_leaves:
        data = read(relative+"/verification.json")
        items = data.get("checks") if isinstance(data.get("checks"), list) else []
        names = {row.get("name") for row in items if isinstance(row, dict)}
        passed = all_checks(data) and len(items) == len(names) == expected
        add("compatibility_"+relative.replace("/", "_")+"_native_checks_passed", passed,
            expected_checks=expected, observed_checks=len(items))
        leaves.append({"group": group, "path": relative, "expected_checks": expected,
                       "observed_checks": len(items),
                       "passed_checks": sum(isinstance(row, dict) and row.get("passed") is True for row in items),
                       "passed": passed})
    suspension_checks, suspension_leaf, suspension_run_id = compatibility_suspension_checks(
        base/"suspension", current, read_report)
    checks.extend(suspension_checks)
    leaves.append(suspension_leaf)
    if suspension_run_id:
        native_run_ids.append(suspension_run_id)
    add("compatibility_each_native_control_case_has_distinct_run_id",
        len(native_run_ids) == len(control_leaves)+1 and len(set(native_run_ids)) == len(native_run_ids))
    totals = {group: {key: sum(row[key] for row in leaves if row["group"] == group)
                      for key in ("expected_checks", "observed_checks", "passed_checks")}
              for group in ("sentry", "manual-waves", "suspension")}
    add("compatibility_all_native_leaf_gates_complete", all(row["passed"] for row in leaves),
        group_check_totals=totals, expected_total_checks=84)
    return checks, {"directory": str(base), "groups": totals, "expected_total_checks": 84, "leaves": leaves}


def gui_render_checks(data, session, gui_prov, gui_report_paths, read_report):
    """Bind actual visible observations to their own complete production clock run."""
    session = project_path(session)
    checks, metrics = [], {"scope": "Visible UE/RViz with the release default RGB capture off; RGB-on realtime and display scanout are unmeasured"}
    def add(name, passed, **detail):
        checks.append({"name": name, "passed": bool(passed), **detail})
    def same_path(value, expected):
        return isinstance(value, str) and bool(value) and project_path(value).resolve() == expected.resolve()
    def selected_file(path_key, hash_key, expected, role):
        binding = same_path(data.get(path_key), expected)
        add(role+"_path_and_sha256_match", binding and expected.is_file()
            and data.get(hash_key) == digest(expected))
        return read_report(expected, role)
    gui_sha = digest(session/"runtime-provenance.json")
    identity = data.get("session_identity")
    identity_valid = (isinstance(identity, list) and len(identity) == 2
                      and all(value is not None for value in identity))
    add("gui_render_report_matches_owned_visible_session",
        same_path(data.get("session_directory"), session) and identity_valid
        and data.get("runtime_provenance_sha256") == gui_sha
        and gui_prov.get("offscreen") is False and gui_prov.get("no_rviz") is False
        and gui_prov.get("no_rhi_diagnostic") is False)
    clock = selected_file("clock_verification_path", "clock_verification_sha256",
                          session/"clock/verification.json", "gui_visible_clock")
    clock_rows = clock.get("checks") if isinstance(clock.get("checks"), list) else []
    clock_names = {row.get("name") for row in clock_rows if isinstance(row, dict)}
    add("gui_visible_clock_all_32_production_checks_passed",
        all_checks(clock, FULL_CLOCK_CHECKS) and len(clock_rows) == 32
        and clock_names == FULL_CLOCK_CHECKS and clock.get("acceptance_scope") == "production_realtime"
        and clock.get("production_eligible") is True and clock.get("measurement_complete") is True
        and finite(clock.get("requested_wall_seconds")) and clock["requested_wall_seconds"] >= 120.
        and finite(clock.get("wall_elapsed_s")) and clock["wall_elapsed_s"] >= 120.,
        observed_checks=len(clock_rows), requested_wall_seconds=clock.get("requested_wall_seconds"))
    add("gui_visible_clock_matches_current_observer_and_owned_session",
        same_path(clock.get("session_directory"), session)
        and clock.get("runtime_provenance_sha256") == gui_sha
        and clock.get("session_identity") == identity
        and clock.get("script_sha256") == digest(ROOT/"tools/integration/measure_console_clock.py"))
    activity = selected_file("gui_activity_evidence_path", "gui_activity_evidence_sha256",
                             session/"gui-activity/verification.json", "gui_activity")
    window = activity.get("gui_activity_window") or {}
    time_keys = ("start_monotonic_s", "end_monotonic_s", "start_sim_time_s", "end_sim_time_s")
    window_valid = (all(finite(window.get(key)) for key in time_keys)
                    and window["end_monotonic_s"] > window["start_monotonic_s"]
                    and window["end_sim_time_s"] > window["start_sim_time_s"])
    add("gui_activity_success_bound_to_same_session",
        activity.get("status") == "passed" and activity.get("mode") == "gui_activity"
        and activity.get("exit_code") == 0 and same_path(activity.get("session_directory"), session)
        and activity.get("runtime_provenance_sha256") == gui_sha
        and activity.get("session_identity") == identity and window_valid)
    command = activity.get("command")
    command = command if isinstance(command, list) and all(isinstance(x, str) for x in command) else []
    lowered = [value.lower() for value in command]
    def option(name):
        if lowered.count(name.lower()) != 1:
            return None
        index = lowered.index(name.lower())
        return command[index+1] if index+1 < len(command) else None
    entry = ROOT/"tools/integration/Run-NavigationConsoleAcceptance.ps1"
    output_value = option("-OutputDirectory")
    activity_output = project_path(output_value) if output_value else None
    command_valid = (any(same_path(token, entry) for token in command)
                     and same_path(option("-SessionDirectory"), session)
                     and not {"-followonly", "-nativeactions", "-skipsingle"} & set(lowered)
                     and activity_output is not None
                     and activity_output.resolve().is_relative_to(session.resolve()))
    selected_paths = {path.resolve() for path in gui_report_paths}
    add("gui_activity_runs_all_original_ui_modes_into_selected_reports",
        command_valid and all((activity_output/mode/"verification.json").resolve() in selected_paths
                              for mode in ("single", "waypoints", "cancel")) if command_valid else False)
    endpoints = {}
    for label, monotonic_key, sim_key in (("before", "start_monotonic_s", "start_sim_time_s"),
                                          ("after", "end_monotonic_s", "end_sim_time_s")):
        path_value = activity.get(label+"_status_path")
        path = project_path(path_value) if isinstance(path_value, str) and path_value else None
        owned = path is not None and path.resolve().is_relative_to((session/"gui-activity").resolve())
        valid_hash = bool(owned and path.is_file() and activity.get(label+"_status_sha256") == digest(path))
        add("gui_activity_"+label+"_snapshot_path_and_hash_match", valid_hash)
        snapshot = read_report(path, "gui_activity_"+label+"_snapshot") if owned else {}
        endpoints[label] = snapshot
        bridge = snapshot.get("bridge") or {}
        sample_wall = snapshot.get("bridge_received_monotonic_s")
        write_wall = snapshot.get("snapshot_written_monotonic_s")
        stamp = bridge.get("sim_time_ns")
        # Snapshots are copied around the actual subprocess boundary. The raw
        # receipt may lie just before/after it; retain the original 1 s budget.
        add("gui_activity_"+label+"_fresh_snapshot_matches_recorded_endpoint",
            window_valid and snapshot.get("phase") == "ready"
            and (snapshot.get("navigation") or {}).get("phase") == "running"
            and bridge.get("connected") is True and bridge.get("state_fresh") is True
            and [bridge.get("session"), bridge.get("epoch")] == identity
            and finite(stamp) and abs(stamp*1.e-9-window[sim_key]) <= 1.e-9
            and finite(sample_wall) and abs(window[monotonic_key]-sample_wall) <= 1.
            and finite(write_wall) and abs(window[monotonic_key]-write_wall) <= 1.)
    interval = {"valid": window_valid, "session_identity": identity, **window}
    add("gui_visible_clock_covers_complete_gui_activity", clock_covers_action(clock, interval),
        gui_activity_window=window)
    trajectories = []
    if command_valid:
        for mode in ("single", "waypoints", "cancel"):
            path = activity_output/mode/"trajectory.json"
            try:
                rows = json.loads(path.read_text(encoding="utf-8-sig"))
                valid = (isinstance(rows, list) and len(rows) > 1
                         and all(isinstance(row, list) and row and finite(row[0]) for row in rows))
                stamps = [row[0] for row in rows] if valid else []
                covered = (valid and all(b >= a for a, b in zip(stamps, stamps[1:]))
                           and finite(clock.get("window_start_sim_time_s"))
                           and finite(clock.get("window_end_sim_time_s"))
                           and clock["window_start_sim_time_s"] <= stamps[0]
                           and stamps[-1] <= clock["window_end_sim_time_s"])
                add("gui_visible_clock_covers_"+mode+"_raw_trajectory", covered)
                trajectories.append({"mode": mode, "path": str(path), "sha256": digest(path),
                                     "samples": len(rows), "start_sim_time_s": stamps[0] if stamps else None,
                                     "end_sim_time_s": stamps[-1] if stamps else None})
            except (OSError, ValueError, TypeError) as error:
                add("gui_visible_clock_covers_"+mode+"_raw_trajectory", False, error=repr(error))
    for field, filename in (("ui_events_sha256", "ui-events.jsonl"), ("action_events_sha256", "action-events.json")):
        path = session/filename
        add("gui_render_"+field+"_matches", path.is_file() and data.get(field) == digest(path))
    final_state = read_report(session/"status.json", "gui_final_camera_state")
    states = [endpoints.get("before", {}), endpoints.get("after", {}), final_state]
    add("gui_declared_rgb_capture_off_matches_observed_state",
        gui_prov.get("camera_hz") == 0 and gui_prov.get("camera_capture_enabled") is False
        and all((state.get("bridge") or {}).get("camera_config", {}).get("capture_enabled") is False
                and (state.get("bridge") or {}).get("camera_config", {}).get("nominal_hz") == 0
                and (state.get("bridge") or {}).get("published_images") == 0 for state in states))
    native_log = session/"runtime.log"
    try:
        native_text = native_log.read_text(encoding="utf-8-sig", errors="replace")
        camera_markers = re.findall(
            r"ROS_CAMERA_CAPTURE enabled=(\d+) nominal_hz=([\d.eE+-]+) width=(\d+) height=(\d+)", native_text)
        command_lines = [line for line in native_text.splitlines() if "LogInit: Command Line:" in line]
        add("gui_native_startup_confirms_rgb_off_and_physics_override",
            bool(camera_markers)
            and all(int(enabled) == 0 and float(hz) == 0.
                    and int(width) == gui_prov.get("camera_width")
                    and int(height) == gui_prov.get("camera_height")
                    for enabled, hz, width, height in camera_markers)
            and gui_prov.get("physics_max_substep_seconds") == .005
            and gui_prov.get("physics_config_override") == PHYSICS_OVERRIDE
            and any(PHYSICS_OVERRIDE in line and re.search(r"(?:^|\s)-RosCameraHz=0(?:\.0+)?(?:\s|$)", line)
                    for line in command_lines),
            runtime_log_sha256=digest(native_log), camera_markers=camera_markers)
    except (OSError, ValueError) as error:
        add("gui_native_startup_confirms_rgb_off_and_physics_override", False, error=repr(error))
    frames = data.get("frames")
    frames = frames if isinstance(frames, list) else []
    frame_records = []
    for index, frame in enumerate(frames):
        frame = frame if isinstance(frame, dict) else {}
        name = frame.get("window")
        pid = frame.get("pid")
        owned_pid = (name in {"ue", "rviz"} and isinstance(pid, int) and pid > 0
                     and all((state.get("pids") or {}).get(name) == pid for state in endpoints.values()))
        path_value = frame.get("path")
        path = project_path(path_value) if isinstance(path_value, str) and path_value else None
        owned_path = path is not None and path.resolve().is_relative_to(session.resolve())
        image_ok = False
        if owned_path and path.is_file():
            raw = path.read_bytes()
            image_ok = (len(raw) >= 24 and raw[:8] == b"\x89PNG\r\n\x1a\n"
                        and int.from_bytes(raw[16:20], "big") > 0
                        and int.from_bytes(raw[20:24], "big") > 0 and frame.get("sha256") == digest(path))
        exact = frame.get("captured_monotonic_s")
        span = frame.get("captured_interval")
        one_time_form = ("captured_monotonic_s" in frame) != ("captured_interval" in frame)
        if finite(exact):
            first = last = exact
        elif isinstance(span, dict) and finite(span.get("start")) and finite(span.get("end")):
            first, last = span["start"], span["end"]
        else:
            first = last = None
        time_ok = (one_time_form and finite(first) and finite(last) and first <= last
                   and finite(clock.get("window_start_monotonic_s"))
                   and finite(clock.get("window_end_monotonic_s"))
                   and clock["window_start_monotonic_s"] <= first
                   and last <= clock["window_end_monotonic_s"])
        if isinstance(span, dict):
            time_ok = (time_ok and name == "rviz" and window_valid
                       and first == window["start_monotonic_s"] and last == window["end_monotonic_s"])
        valid = owned_pid and image_ok and time_ok
        add("gui_visible_frame_"+str(index+1)+"_owned_hashed_and_clock_covered", valid,
            window=name, path=str(path) if path else None, capture_interval_monotonic_s=[first, last])
        frame_records.append({"window": name, "path": str(path) if path else None,
                              "sha256": frame.get("sha256"), "capture_start": first, "capture_end": last,
                              "passed": valid})
    ue_frames = [frame for frame in frame_records if frame["window"] == "ue" and frame["passed"]]
    rviz_frames = [frame for frame in frame_records if frame["window"] == "rviz" and frame["passed"]]
    add("gui_visible_frame_sequence_includes_ue_and_rviz",
        len(ue_frames) >= 2 and bool(rviz_frames)
        and len({frame["sha256"] for frame in ue_frames}) >= 2
        and len({frame["capture_start"] for frame in ue_frames}) >= 2
        and all(frame["passed"] for frame in frame_records),
        scope="Distinct samples support inspection; navigation motion and absence of blank frames require the explicit reviewed checks.")
    metrics.update(clock_verification_path=str(session/"clock/verification.json"),
                   clock_verification_sha256=data.get("clock_verification_sha256"),
                   measured_realtime_factor=clock.get("measured_realtime_factor"),
                   wall_elapsed_s=clock.get("wall_elapsed_s"), session_identity=identity,
                   gui_activity_window=window, trajectories=trajectories, frames=frame_records,
                   rendering=clock.get("rendering"), sensor_rates=clock.get("sensor_rates"))
    return checks, metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wave-session", type=Path, action="append", default=[])
    parser.add_argument("--top-speed-session", type=Path)
    parser.add_argument("--top-speed-report", type=Path)
    parser.add_argument("--clock", type=Path, action="append", default=[])
    parser.add_argument("--imu-receipts", type=Path, action="append", default=[])
    parser.add_argument("--engine-build", type=Path)
    parser.add_argument("--bridge-build", type=Path)
    parser.add_argument("--pointlio-build", type=Path)
    parser.add_argument("--offline", type=Path)
    parser.add_argument("--compatibility", type=Path)
    parser.add_argument("--oracle", type=Path)
    parser.add_argument("--gui-session", type=Path)
    parser.add_argument("--gui", type=Path, action="append", default=[])
    parser.add_argument("--history", type=Path, action="append", default=[])
    parser.add_argument("--navigation-source", type=Path,
                        default=ROOT/"ros2_ws/src/pb2025_nav_bringup/config/simulation/nav2_params.yaml")
    parser.add_argument("--localization-map-yaml", type=Path, help="Declared final RMUC AMCL map; omit to verify the current packaged default")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = project_path(args.output)
    if output.exists():
        raise FileExistsError("Preserve earlier summaries; choose a fresh output: "+str(output))
    result = {
        "status": "in_progress", "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "Windows native AIM sentry source-owned capped navigation, measured realtime and visible GUI; Ubuntu, multi-robot, full field and physical calibration remain separate",
        "checks": [], "evidence": [], "historical_evidence": [], "metrics": {},
        "command": [sys.executable, *sys.argv],
        "summarizer_sha256": digest(Path(__file__)),
    }

    def check(name, passed, **details):
        result["checks"].append({"name": name, "passed": bool(passed), **details})

    def read(path, role, historical=False):
        if path is None:
            check(role+"_provided", False)
            return {}
        path = report_path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            decoded_checks = data.get("checks")
            check_rows = decoded_checks if isinstance(decoded_checks, list) else []
            item = {"role": role, "path": str(path), "sha256": digest(path),
                    "status": data.get("status"),
                    "failed_checks": [c.get("name") for c in check_rows
                                      if isinstance(c, dict) and c.get("passed") is not True]}
            if data.get("error"):
                item["error"] = data["error"]
            result["historical_evidence" if historical else "evidence"].append(item)
            return data
        except (OSError, ValueError, TypeError) as error:
            check(role+"_readable", False, path=str(path), error=repr(error))
            return {}

    try:
        import yaml
        source = project_path(args.navigation_source)
        source_data = yaml.safe_load(source.read_text(encoding="utf-8-sig"))
        controller = source_data["controller_server"]["ros__parameters"]["FollowPath"]
        smoother = source_data["velocity_smoother"]["ros__parameters"]
        source_hash = digest(source)
        current = {key: digest(ROOT/path) for key, path in IDENTITY_FILES.items()}
        current["source_control_config_sha256"] = source_hash
        current["module_sha256"] = digest(ROOT/"unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll")
        result["declared_final_identity"] = current
        result["declared_navigation_source"] = str(source)
        result["declared_localization_map_yaml"] = str(project_path(args.localization_map_yaml)) if args.localization_map_yaml else None
        result["git_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        drive = json.loads((ROOT/IDENTITY_FILES["drive_profile_sha256"]).read_text(encoding="utf-8-sig"))
        lidar = json.loads((ROOT/IDENTITY_FILES["lidar_sha256"]).read_text(encoding="utf-8-sig"))
        imu = json.loads((ROOT/IDENTITY_FILES["imu_sha256"]).read_text(encoding="utf-8-sig"))
        check("accepted_vehicle_profile_and_2000_140_suspension_preserved",
              drive["physics"]["SpringStiffnessNPerM"] == 2000
              and drive["physics"]["SpringDampingNsPerM"] == 140,
              profile_sha256=current["drive_profile_sha256"])
        check("sentry_source_forward_cap_matches_current_manual_profile",
              finite(controller["v_linear_max"]) and controller["v_linear_max"] > 0
              and controller["v_linear_max"] == drive["control"]["MaxForwardSpeedMps"]
              and smoother["max_velocity"] == [controller["v_linear_max"], 2., 3.]
              and smoother["min_velocity"] == [-1.5, -2., -3.],
              source_sha256=source_hash, source_cap_mps=controller["v_linear_max"],
              smoother_max_velocity=smoother["max_velocity"], smoother_max_accel=smoother["max_accel"],
              smoother_max_decel=smoother["max_decel"])
        check("production_mounted_sensor_budgets_preserved",
              lidar["rays_per_second"] == 200000 and lidar["frame_hz"] == 10
              and lidar["parent_frame"] == "yaw_link"
              and any(s["id"] == "mid360_imu" and s["rate_hz"] == 200 and
                      s["parent_frame"] == "yaw_link" for s in imu["imus"]))

        engine = read(args.engine_build, "engine_build")
        check("native_engine_build_passed", engine.get("status") == "passed" and engine.get("exit_code") == 0
              and engine.get("module_sha256") == current["module_sha256"])
        engine_inputs = {k.replace("\\", "/"): v for k, v in engine.get("source_sha256", {}).items()}
        actual_engine_inputs = {p.relative_to(ROOT).as_posix(): digest(p)
                               for p in (ROOT/"unreal/RMSim/Source").rglob("*")
                               if p.is_file() and p.suffix in (".cpp", ".h", ".cs")}
        # Build provenance includes the project descriptor and invocation script.
        for build_input in ("unreal/RMSim/RMSim.uproject", "tools/unreal/Build-ImportLab.ps1"):
            actual_engine_inputs[build_input] = digest(ROOT/build_input)
        check("engine_build_input_manifest_stable_during_build",
              engine.get("inputs_unchanged_during_build") is True
              and engine.get("source_after_sha256") == engine.get("source_sha256"))
        check("engine_build_matches_every_current_source_file", bool(engine_inputs)
              and engine_inputs == actual_engine_inputs,
              missing=sorted(set(actual_engine_inputs)-set(engine_inputs)),
              changed=sorted(k for k in engine_inputs if engine_inputs[k] != actual_engine_inputs.get(k)))

        bridge = read(args.bridge_build, "bridge_build")
        source_root = ROOT/"ros2_ws/src/rmsim_bridge/rmsim_bridge"
        install_root = ROOT/"ros2_ws/install_bridge/rmsim_bridge/Lib/site-packages/rmsim_bridge"
        current_modules = {p.relative_to(source_root).as_posix():
                           {"source_sha256": digest(p),
                            "installed_sha256": digest(install_root/p.relative_to(source_root))}
                           for p in source_root.rglob("*.py")}
        bridge_modules = bridge.get("modules", {})
        check("native_bridge_build_and_installed_sources_match",
              bridge.get("status") == "passed" and bridge.get("exit_code") == 0
              and set(bridge_modules) == set(current_modules)
              and all(bridge_modules.get(name, {}).get("text_equal") is True
                      and bridge_modules[name].get("source") == row["source_sha256"]
                      and bridge_modules[name].get("installed") == row["installed_sha256"]
                      for name, row in current_modules.items()))
        result["bridge_identity"] = current_modules
        current_pointlio = capture_pointlio_inputs(ROOT, ROOT/"ros2_ws/install_lio/point_lio")
        result["pointlio_identity"] = current_pointlio
        pointlio_build = read(args.pointlio_build, "pointlio_build")
        check("native_pointlio_build_passed_and_matches_current_inputs",
              pointlio_build.get("status") == "passed" and pointlio_build.get("exit_code") == 0
              and pointlio_build.get("pointlio_inputs") == current_pointlio)

        def provenance(session, role):
            session = project_path(session)
            path = session/"runtime-provenance.json"
            data = read(path, role+"_provenance")
            mismatches = [key for key, value in current.items() if data.get(key) != value]
            check(role+"_matches_final_inputs", not mismatches and bool(data),
                  mismatches=mismatches, provenance_sha256=digest(path) if path.is_file() else None)
            check(role+"_uses_current_installed_bridge", data.get("bridge_inputs") == current_modules)
            check(role+"_uses_current_pointlio_binary_sources_patch_and_base_config",
                  data.get("pointlio_inputs") == current_pointlio)
            native = data.get("native_navigation", {})
            check(role+"_native_navigation_binaries_match", bool(native) and all(
                digest(ROOT/"artifacts/runtime/navigation/windows"/name) == sha for name, sha in native.items()))
            check(role+"_production_sensor_and_clock_configuration",
                  data.get("sim_step_seconds") == .005 and data.get("no_rhi_diagnostic") is False
                  and data.get("pace_real_time") is True and data.get("viewport_hz") == 60.)
            check(role+"_declares_release_rgb_off_and_navigation_physics_override",
                  data.get("camera_hz") == 0 and data.get("camera_capture_enabled") is False
                  and data.get("physics_max_substep_seconds") == .005
                  and data.get("physics_config_override") == PHYSICS_OVERRIDE,
                  scope="This release's measured navigation profile disables unused RGB readback; RGB-on realtime remains unmeasured.")
            policy = read(session/"process-policy/verification.json", role+"_process_policy")
            policy_status = read(session/"status.json", role+"_policy_session_status")
            for name, passed in cpu_policy_checks(policy, data, session, policy_status, current["cpu_policy_source_sha256"]).items():
                check(role+"_"+name, passed)
            runtime_path = session/"runtime.log"
            runtime_text = runtime_path.read_text(encoding="utf-8-sig", errors="replace") if runtime_path.is_file() else ""
            for name, passed in worker_budget_checks(policy, data, runtime_text).items():
                check(role+"_"+name, passed)
            if runtime_path.is_file():
                result["evidence"].append({"role":role+"_worker_budget_native_runtime", "path":str(runtime_path),
                                          "sha256":digest(runtime_path), "scope":"UE startup Cores marker; worker thread count is expected from source, not directly observed"})
            map_source = ROOT/"ros2_ws/src/rmsim_bridge/rmsim_bridge/navigation_map_identity.py"
            spec = importlib.util.spec_from_file_location("rmsim_release_map_identity", map_source)
            map_helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(map_helper)
            map_checks, map_details = localization_map_checks(data, args.localization_map_yaml, map_helper)
            for name, passed in map_checks.items():
                check(role+"_"+name, passed, **map_details)
            if "package_manifest" in map_details:
                result["evidence"].append({"role":role+"_localization_package_manifest", **map_details["package_manifest"]})
            actual_localization = data.get("localization_map")
            if isinstance(actual_localization, dict):
                native_map_state = policy_status.get("navigation") or {}
                check(role+"_both_published_maps_verified_by_native_navigation",
                      native_map_state.get("localization_map_verified") is True
                      and native_map_state.get("map_input_fault") == ""
                      and native_map_state.get("localization_map") == actual_localization)
            return data

        waves = []
        check("at_least_one_full_wave_run_selected", bool(args.wave_session))
        for index, session in enumerate(args.wave_session):
            session = project_path(session)
            role = "wave_"+str(index+1)
            prov = provenance(session, role)
            action = read(session/"actions/verification.json", role+"_actions")
            check(role+"_all_fourteen_original_gates_passed", all_checks(action, WAVE_CHECKS))
            check(role+"_normal_acceptance_has_optional_xyz_capture_disabled",
                  action.get("localization_cloud_capture", {}).get("enabled") is False)
            single = check_named(action, "single_goal_actual_arrival")
            multi = check_named(action, "waypoint_sequence_actual_arrival")
            stop = check_named(action, "cancelled_vehicle_stopped")
            check(role+"_physical_tolerances_preserved",
                  finite(single.get("error_m")) and single["error_m"] < .25
                  and finite(multi.get("error_m")) and multi["error_m"] < .25
                  and multi.get("missed_waypoints") == []
                  and finite(stop.get("braking_m")) and stop["braking_m"] < .2
                  and finite(stop.get("drift_m")) and stop["drift_m"] < .02)
            nav = action.get("last_navigation") or {}
            session_state = read(session/"status.json", role+"_session_status")
            check(role+"_action_and_session_identity_match", nav.get("identity") is not None
                  and nav.get("identity") == (session_state.get("navigation") or {}).get("identity"))
            check(role+"_uses_declared_sentry_source", nav.get("map_id") == "rmuc2027"
                  and nav.get("control_source", {}).get("source_sha256") == source_hash
                  and nav.get("navigation_velocity_limit_mps") == controller["v_linear_max"]
                  and nav.get("smoother_max_velocity") == smoother["max_velocity"]
                  and nav.get("smoother_max_accel") == smoother["max_accel"]
                  and nav.get("smoother_max_decel") == smoother["max_decel"])
            check(role+"_observer_script_matches", action.get("script_sha256") ==
                  digest(ROOT/"tools/integration/probe_high_speed_diagnostics.py"))
            trace = read(session/"actions/trace.json", role+"_action_trace")
            interval = action_trace_interval(trace, nav.get("identity"))
            check(role+"_action_trace_has_complete_monotonic_interval", interval["valid"],
                  interval=interval)
            waves.append({"directory": str(session), "identity": nav.get("identity"),
                          "action_interval": interval,
                          "provenance_sha256": digest(session/"runtime-provenance.json") if prov else None,
                          "single_error_m": single.get("error_m"), "waypoint_error_m": multi.get("error_m"),
                          "cancel_distance_m": stop.get("braking_m")})
        result["metrics"]["wave_runs"] = waves

        speed_prov = provenance(args.top_speed_session, "top_speed") if args.top_speed_session else {}
        speed = read(args.top_speed_report, "top_speed")
        contract = speed.get("contract") or {}
        if args.top_speed_session:
            speed_state = read(project_path(args.top_speed_session)/"status.json", "top_speed_session_status")
            identity = (speed.get("last_navigation") or {}).get("identity")
            check("top_speed_action_and_requested_session_identity_match", identity is not None
                  and identity == (speed_state.get("navigation") or {}).get("identity"))
            recorded_hash = speed.get("runtime_provenance_sha256")
            check("top_speed_recorded_provenance_hash_matches", recorded_hash ==
                  digest(project_path(args.top_speed_session)/"runtime-provenance.json"))
        check("top_speed_observer_script_matches", speed.get("script_sha256") ==
              digest(ROOT/"tools/integration/probe_navigation_top_speed.py"))
        check("top_speed_all_original_gates_passed", all_checks(speed, SPEED_CHECKS))
        check("top_speed_all_five_staging_gates_passed", all_checks(speed, STAGING_CHECKS))
        speed_trace_path = report_path(args.top_speed_report).parent/"trace.json"
        speed_trace = read(speed_trace_path, "top_speed_phase_trace")
        check("top_speed_raw_trace_hash_and_runtime_binding",
              speed.get("trace_sha256") == digest(speed_trace_path)
              and speed_trace.get("metadata", {}).get("script_sha256") == speed.get("script_sha256")
              and speed_trace.get("metadata", {}).get("runtime_provenance_sha256") == speed.get("runtime_provenance_sha256")
              and speed_trace.get("metadata", {}).get("source_sha256") == source_hash
              and speed_trace.get("metadata", {}).get("scene_sha256") == speed_prov.get("scene", {}).get("scene_sha256"))
        phase_checks, phase_metrics = top_speed_phase_checks(
            speed, speed_trace, (speed.get("last_navigation") or {}).get("identity"))
        for name, passed in phase_checks.items():
            check(name, passed)
        result["metrics"]["top_speed_phase_audit"] = phase_metrics
        if args.top_speed_session:
            from probe_navigation_top_speed import source_contract, speed_lane_contract
            reproduced_contract = source_contract(project_path(args.top_speed_session))
            check("top_speed_current_scene_and_source_contract_reproduced",
                  contract == {k: v for k, v in reproduced_contract.items() if k != "provenance"})
            scene_path = project_path(speed_prov["scene"]["scene_file"])
            scene_data = json.loads(scene_path.read_text(encoding="utf-8-sig"))
            measured_lane = speed_lane_contract(
                scene_data, speed["formal_measurement"]["actual_start_position_m"][:2],
                contract["goal"], contract["swept_radius_m"], contract["source_cap_mps"],
                contract["source_accel_mps2"], contract["approach_velocity_scaling_dist_m"])
            check("top_speed_staged_actual_lane_has_unchanged_source_length_and_collision_budget",
                  speed.get("measured_lane") == measured_lane)
        check("top_speed_threshold_uses_same_declared_source_cap",
              contract.get("source_sha256") == source_hash and contract.get("source_cap_mps") == controller["v_linear_max"]
              and finite(contract.get("speed_threshold_mps"))
              and abs(contract["speed_threshold_mps"]-.8*controller["v_linear_max"]) < 1.e-9
              and contract.get("sustained_seconds") == .3
              and contract.get("source_smoother_max_accel") == smoother["max_accel"]
              and contract.get("source_smoother_max_decel") == smoother["max_decel"]
              and speed_prov.get("scene", {}).get("scene") == "import_lab")
        tracking = check_named(speed, "source_command_reaches_wheel_target_without_second_shaper")
        check("external_source_commands_reach_wheel_target_directly",
              tracking.get("passed") is True and tracking.get("samples", 0) >= 20
              and finite(tracking.get("max_linear_error_mps")) and tracking["max_linear_error_mps"] <= 1.e-6
              and finite(tracking.get("max_yaw_error_rad_s")) and tracking["max_yaw_error_rad_s"] <= 1.e-6)
        result["metrics"]["top_speed"] = {"actual_peak_mps": speed.get("actual_peak_speed_mps"),
            "required_sustained_mps": contract.get("speed_threshold_mps"),
            "required_sustained_seconds": contract.get("sustained_seconds"),
            "cancellation": speed.get("cancellation"), "tracking": tracking}

        check("production_clock_report_selected", bool(args.clock))
        covered_waves = set()
        result["metrics"]["clocks"] = []
        for index, path in enumerate(args.clock):
            role = "clock_"+str(index+1)
            clock = read(path, role)
            check(role+"_production_acceptance_passed", all_checks(clock, FULL_CLOCK_CHECKS)
                  and clock.get("acceptance_scope") == "production_realtime"
                  and clock.get("production_eligible") is True and clock.get("measurement_complete") is True
                  and clock.get("requested_wall_seconds", 0) >= 60.)
            matched = [w for w in waves if clock.get("runtime_provenance_sha256") == w["provenance_sha256"]
                       and clock.get("session_identity") == w["identity"]]
            check(role+"_bound_to_selected_wave_run", bool(matched))
            covered = [w for w in matched if clock_covers_action(clock, w["action_interval"])]
            check(role+"_covers_complete_action_trace", bool(matched) and len(covered) == len(matched),
                  clock_wall_interval=[clock.get("window_start_monotonic_s"), clock.get("window_end_monotonic_s")],
                  clock_sim_interval=[clock.get("window_start_sim_time_s"), clock.get("window_end_sim_time_s")],
                  action_intervals=[{"directory": w["directory"], **w["action_interval"]} for w in matched])
            covered_waves.update(w["directory"] for w in covered)
            check(role+"_observer_script_matches", clock.get("script_sha256") ==
                  digest(ROOT/"tools/integration/measure_console_clock.py"))
            result["metrics"]["clocks"].append({key: clock.get(key) for key in
                ("measured_realtime_factor", "wall_elapsed_s", "windows", "rolling_windows",
                 "rolling_window_stride_seconds", "window_start_monotonic_s", "window_end_monotonic_s",
                 "window_start_sim_time_s", "window_end_sim_time_s", "rendering", "sensor_rates", "session_identity")})
        check("each_selected_wave_run_has_production_clock_evidence",
              bool(waves) and covered_waves == {w["directory"] for w in waves})

        check("native_imu_receipt_reports_selected", bool(args.imu_receipts))
        receipt_waves = set()
        result["metrics"]["imu_receipts"] = []
        selected_depth = (source_data.get("point_lio", {}).get("ros__parameters", {})
                          .get("common", {}).get("imu_qos_depth"))
        for index, path in enumerate(args.imu_receipts):
            role = "imu_receipts_"+str(index+1)
            first_check = len(result["checks"])
            data = read(path, role)
            check(role+"_all_native_receive_checks_passed", all_checks(data, IMU_RECEIPT_CHECKS))
            declared_session = project_path(data["session_directory"]) if data.get("session_directory") else None
            matched = [w for w in waves if declared_session == project_path(w["directory"])
                       and data.get("runtime_provenance_sha256") == w["provenance_sha256"]
                       and data.get("native_sensor_identity") == w["identity"]]
            check(role+"_bound_to_selected_wave_session", bool(matched))
            check(role+"_matches_current_pointlio_binary_sources_and_patch",
                  data.get("pointlio_inputs") == current_pointlio
                  and data.get("runtime_identity_source_sha256") == current["runtime_identity_source_sha256"])
            if not matched:
                continue
            wave = matched[0]
            session = project_path(wave["directory"])
            check(role+"_binds_actual_action_report_and_full_trace",
                  data.get("action_verification_sha256") == digest(session/"actions/verification.json")
                  and data.get("action_trace_sha256") == digest(session/"actions/trace.json")
                  and data.get("action_script_sha256") == digest(ROOT/"tools/integration/probe_high_speed_diagnostics.py"))
            log = project_path(data.get("native_log_path", ""))
            owned_log = log.is_relative_to(session/"pointlio") and log.is_file()
            check(role+"_native_log_belongs_to_selected_session", owned_log)
            if not owned_log:
                continue
            check(role+"_final_native_log_hash_matches", data.get("native_log_sha256") == digest(log))
            parameters = read(log.with_suffix(".yaml"), role+"_native_parameters")
            native_status = read(log.parent/"status.json", role+"_native_status")
            check(role+"_native_parameter_and_status_hashes_match",
                  data.get("native_parameter_file_sha256") == digest(log.with_suffix(".yaml"))
                  and data.get("native_status_file_sha256") == digest(log.parent/"status.json"))
            check(role+"_native_runner_session_and_source_match",
                  native_status.get("identity") == wave["identity"]
                  and data.get("native_source_identity") == native_status.get("source_identity")
                  and (native_status.get("source_identity") or {}).get("sha256") == source_hash)
            native_text = log.read_text(encoding="utf-8-sig", errors="replace")
            native_depths = [int(value) for value in re.findall(r"POINTLIO_IMU_QOS depth=(\d+)", native_text)]
            check(role+"_current_source_owns_actual_receive_depth",
                  type(selected_depth) is int and selected_depth > 0
                  and data.get("expected_imu_qos_depth") == selected_depth
                  and parameters.get("/**", {}).get("ros__parameters", {}).get("common.imu_qos_depth") == selected_depth
                  and bool(native_depths) and all(value == selected_depth for value in native_depths))
            for item in receipt_counter_checks(data, wave["action_interval"], native_text):
                check(role+"_"+item["name"], item["passed"],
                      **{key: value for key, value in item.items() if key not in ("name", "passed")})
            if all(item["passed"] for item in result["checks"][first_check:]):
                receipt_waves.add(wave["directory"])
            result["metrics"]["imu_receipts"].append({
                "session_directory": str(session), "session_identity": data.get("native_sensor_identity"),
                "native_log_sha256": data.get("native_log_sha256"),
                "enclosing_counter_interval": data.get("enclosing_counter_interval")})
        check("each_selected_wave_has_loss_free_native_imu_receipts",
              bool(waves) and receipt_waves == {w["directory"] for w in waves})

        check("native_compatibility_report_selected", args.compatibility is not None)
        if args.compatibility is not None:
            compatibility, metrics = compatibility_checks(args.compatibility, current, current_pointlio, read)
            for item in compatibility:
                check(item["name"], item["passed"],
                      **{key: value for key, value in item.items() if key not in ("name", "passed")})
            result["metrics"]["compatibility"] = metrics

        offline = read(args.offline, "offline")
        files = offline.get("files", [])
        tests = {row["path"]: row["sha256"] for row in files} if isinstance(files, list) else files
        check("offline_suite_passed", offline.get("status") == "passed"
              and offline.get("tests_run", 0) > 0 and offline.get("failures") == 0 and offline.get("errors") == 0)
        required_tests = {"tests/control/test_console_clock.py", "tests/control/test_navigation_speed.py"}
        check("current_clock_and_source_tests_were_included", required_tests <= set(tests)
              and all(digest(ROOT/name) == sha for name, sha in tests.items())
              and not any("ConsoleClock" in name or "SentryNavigationSource" in name
                          for name in offline.get("skipped_tests", [])))
        result["metrics"]["offline"] = {key: offline.get(key) for key in
            ("tests_run", "passed", "skipped", "failures", "errors", "skipped_tests")}

        oracle = read(args.oracle, "oracle")
        check("native_lidar_oracle_passed", all_checks(oracle))
        oracle_prov_path = oracle.get("runtime_provenance_path") or oracle.get("provenance_path")
        if oracle_prov_path:
            opath = project_path(oracle_prov_path)
            oprov = provenance(opath.parent, "oracle")
            declared_sha = oracle.get("runtime_provenance_sha256") or oracle.get("provenance_sha256")
            check("oracle_bound_to_final_runtime", declared_sha == digest(opath)
                  and oracle.get("module_sha256") == current["module_sha256"]
                  and oprov.get("verify_lidar_queries") is True)
            log = opath.parent/"runtime.log"
            text = log.read_text(encoding="utf-8-sig", errors="replace")
            check("oracle_final_native_log_hash_matches",
                  oracle.get("runtime_log_sha256") == digest(log)
                  and bool(oracle.get("runtime_log_path"))
                  and project_path(oracle["runtime_log_path"]) == log)
            matches = re.findall(r"LIDAR_QUERY_VERIFICATION result=passed rays=(\d+) scene_queries=(\d+) max_impact_error_cm=([\d.eE+-]+)", text)
            check("native_oracle_log_compares_all_4096_rays", bool(matches)
                  and any(int(rays) == 4096 and int(queries) >= 4096 and float(error) <= 1.e-4
                          for rays, queries, error in matches)
                  and "LIDAR_QUERY_VERIFICATION result=failed" not in text,
                  log_sha256=digest(log), observed_results=matches)
        else:
            check("oracle_provenance_binding_present", False)

        gui_prov = provenance(args.gui_session, "gui") if args.gui_session else {}
        check("visible_gui_session_selected", bool(gui_prov) and gui_prov.get("offscreen") is False
              and gui_prov.get("no_rviz") is False)
        modes = set()
        action_ids = set()
        gui_sha = None
        if args.gui_session:
            session = project_path(args.gui_session)
            gui_sha = digest(session/"runtime-provenance.json")
            events = json.loads((session/"action-events.json").read_text(encoding="utf-8-sig"))
            action_ids = {g["id"] for row in events for g in row.get("goals", [])}
            ui = [json.loads(line) for line in (session/"ui-events.jsonl").read_text(encoding="utf-8-sig").splitlines() if line.strip()]
            check("actual_rviz_os_input_and_cancel_recorded",
                  any(row.get("action") == "Goal" and row.get("foreground") is True
                      and row.get("input_route") == "actual_OS_mouse_after_foreground_and_pixel_owner_validation" for row in ui)
                  and any(row.get("action") == "Button" and row.get("button") == "Cancel" for row in ui))
        for index, path in enumerate(args.gui):
            role = "gui_report_"+str(index+1)
            data = read(path, role)
            mode = data.get("mode")
            modes.add(mode)
            check(role+"_actual_checks_passed",
                  all_checks(data, RENDER_CHECKS if mode == "render" else GUI_CHECKS.get(mode)))
            if mode in GUI_CHECKS:
                goal = check_named(data, "rviz_action_reaches_original_nav2").get("goal_id")
                check(role+"_bound_to_gui_session_action", goal is not None and goal in action_ids)
            elif mode == "render":
                check(role+"_visible_frames_bound_to_gui_session",
                      data.get("runtime_provenance_sha256") == gui_sha and bool(gui_sha))
                render_checks, render_metrics = gui_render_checks(
                    data, session, gui_prov,
                    [report_path(project_path(path)) for path in args.gui], read)
                result["checks"].extend(render_checks)
                result["metrics"]["visible_gui"] = render_metrics
            else:
                check(role+"_recognized_actual_gui_scope", False, mode=mode)
        check("single_waypoints_cancel_and_visible_frame_observations_present",
              {"single", "waypoints", "cancel", "render"} <= modes)

    except Exception as error:
        check("aggregation_completed_without_missing_or_invalid_inputs", False, error=repr(error))
    finally:
        for index, path in enumerate(args.history):
            read(path, "history_"+str(index+1), historical=True)
        result["gaps"] = [row for row in result["checks"] if not row["passed"]]
        result["status"] = "accepted" if result["checks"] and not result["gaps"] else "in_progress"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        print(json.dumps({"status": result["status"], "output": str(output),
                          "checks": len(result["checks"]), "open_checks": len(result["gaps"])},
                         ensure_ascii=False, indent=2), flush=True)
    return 0 if result["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
