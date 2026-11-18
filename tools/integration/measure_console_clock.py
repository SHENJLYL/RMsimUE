"""Measure native simulation time against producer-associated monotonic wall time.

This observer never writes to ROS or the simulator. A production pass requires a
60-second window, 0.98..1.02 throughput overall and in 10-second rolling windows
at one-second strides (plus the final complete window),
fresh stable-session telemetry, and intact 200 kHz LiDAR / 200 Hz IMU sampling.
Native scene Draw counters must sustain 60 Hz within 5%; visible frame retention
and physical display scanout require separate GUI evidence.
"""
from __future__ import annotations
import argparse
import bisect
import datetime
import hashlib
import json
import math
from pathlib import Path
import sys
import time

RATE_MIN, RATE_MAX = .98, 1.02
MAX_FRESHNESS_S = 1.
WINDOW_S = 10.
ROLLING_STRIDE_S = 1.
IMU_COUNTERS = ("measured_samples", "missed_periods", "dropped_samples",
                "history_resets", "invalid_samples")
LIDAR_COUNTERS = ("traced_rays", "published_scans", "dropped_scans", "history_resets")


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def observation(data, observed):
    """Keep evidence needed to reconstruct decisions, including repeated snapshots."""
    bridge = data.get("bridge") or {}
    return {
        "observed_monotonic_s": observed,
        "snapshot_written_monotonic_s": data.get("snapshot_written_monotonic_s"),
        "snapshot_sequence": data.get("snapshot_sequence"),
        "bridge_received_monotonic_s": data.get("bridge_received_monotonic_s"),
        "phase": data.get("phase"),
        "navigation_phase": (data.get("navigation") or {}).get("phase"),
        "session": bridge.get("session"), "epoch": bridge.get("epoch"),
        "sim_time_ns": bridge.get("sim_time_ns"),
        "connected": bridge.get("connected"), "state_fresh": bridge.get("state_fresh"),
        "fixed_step_s": bridge.get("fixed_step_s"),
        "reported_simulation_rate": bridge.get("simulation_rate"),
        "imu_stats": (bridge.get("imu") or {}).get("stats"),
        "lidar_stats": {k: (bridge.get("lidar") or {}).get(k) for k in LIDAR_COUNTERS},
        "rendering": bridge.get("rendering"),
        "viewport_hz": data.get("viewport_hz"),
        "pace_real_time": data.get("pace_real_time"),
        "no_rhi_diagnostic": data.get("no_rhi_diagnostic"),
    }


def read_observation(session):
    observed = time.monotonic()
    try:
        data = json.loads((session / "status.json").read_text(encoding="utf-8-sig"))
        return observation(data, time.monotonic())
    except (OSError, json.JSONDecodeError) as error:
        return {"observed_monotonic_s": observed, "read_error": repr(error)}


def ready(row):
    fields = ("bridge_received_monotonic_s", "snapshot_written_monotonic_s", "sim_time_ns")
    return (all(finite(row.get(k)) for k in fields)
            and row.get("phase") == "ready" and row.get("navigation_phase") == "running"
            and row.get("connected") is True and row.get("state_fresh") is True
            and row.get("session") is not None and row.get("epoch") is not None
            and 0 <= row["observed_monotonic_s"] - row["bridge_received_monotonic_s"] <= MAX_FRESHNESS_S
            and 0 <= row["observed_monotonic_s"] - row["snapshot_written_monotonic_s"] <= MAX_FRESHNESS_S)


def analyze_observations(rows, seconds=60., expected_rays_per_second=200000.,
                         expected_imu_hz=200., expected_scan_hz=10., diagnostic=False):
    checks = []
    def check(name, passed, **detail):
        checks.append({"name": name, "passed": bool(passed), **detail})

    schema_rows = [r for r in rows if all(finite(r.get(k)) for k in
                   ("observed_monotonic_s", "snapshot_written_monotonic_s",
                    "bridge_received_monotonic_s", "sim_time_ns"))]
    check("producer_monotonic_telemetry_present", len(schema_rows) > 10,
          valid_rows=len(schema_rows), observations=len(rows),
          read_errors=sum("read_error" in r for r in rows))
    check("every_decoded_snapshot_has_clock_metadata",
          len(schema_rows) == sum("read_error" not in r for r in rows))
    check("production_measurement_contract",
          diagnostic or (seconds >= 60 and expected_rays_per_second == 200000
                         and expected_imu_hz == 200 and expected_scan_hz == 10),
          diagnostic_sensor_budget=diagnostic, requested_seconds=seconds,
          expected_rays_per_second=expected_rays_per_second,
          expected_imu_hz=expected_imu_hz, expected_scan_hz=expected_scan_hz)
    report = {"status": "incomplete", "acceptance_scope":
              "diagnostic_realtime" if diagnostic else "production_realtime",
              "production_eligible": not diagnostic, "checks": checks,
              "measurement_basis": "simulation stamps paired with console bridge-receipt monotonic stamps",
              "visual_scope": "native scene Draw-call counter per monotonic wall second; display scanout and frame retention require separate GUI evidence",
              "samples": len(schema_rows), "requested_wall_seconds": seconds,
              "measured_realtime_factor": None, "windows": [], "rolling_windows": [],
              "rolling_window_stride_seconds": ROLLING_STRIDE_S}
    if len(schema_rows) < 2:
        return report

    identities = {(r.get("session"), r.get("epoch")) for r in schema_rows}
    check("session_identity_constant", len(identities) == 1 and
          all(a is not None and b is not None for a, b in identities),
          identities=[list(x) for x in identities])
    check("sim_clock_never_regresses", all(b["sim_time_ns"] >= a["sim_time_ns"]
          for a, b in zip(schema_rows, schema_rows[1:])))
    check("producer_clock_never_regresses", all(
          b["bridge_received_monotonic_s"] >= a["bridge_received_monotonic_s"]
          and b["snapshot_written_monotonic_s"] >= a["snapshot_written_monotonic_s"]
          for a, b in zip(schema_rows, schema_rows[1:])))
    check("ready_and_fresh_for_entire_window", all(ready(r) for r in schema_rows),
          maximum_bridge_age_s=max(r["observed_monotonic_s"]-r["bridge_received_monotonic_s"] for r in schema_rows),
          maximum_snapshot_age_s=max(r["observed_monotonic_s"]-r["snapshot_written_monotonic_s"] for r in schema_rows))
    check("no_observation_gap_over_one_second", all(
          b["observed_monotonic_s"] - a["observed_monotonic_s"] <= MAX_FRESHNESS_S
          for a, b in zip(schema_rows, schema_rows[1:])))
    check("production_visual_configuration", diagnostic or all(
          r.get("no_rhi_diagnostic") is False and r.get("pace_real_time") is True
          and finite(r.get("viewport_hz")) and r["viewport_hz"] >= 60 for r in schema_rows),
          required_viewport_hz=60, check_scope="configuration only")

    # Validate the raw sequence above BEFORE removing repeated file snapshots.
    unique = []
    for row in schema_rows:
        if not unique or row["bridge_received_monotonic_s"] != unique[-1]["bridge_received_monotonic_s"]:
            unique.append(row)
        elif row["sim_time_ns"] != unique[-1]["sim_time_ns"]:
            check("duplicate_producer_stamp_has_same_sim_time", False)
    ts = [r["bridge_received_monotonic_s"] for r in unique]
    start, finish = ts[0], ts[0] + seconds
    order = all(a < b for a, b in zip(ts, ts[1:]))
    complete = order and ts[-1] >= finish and len(unique) > 10
    check("complete_requested_window", complete, sampled_wall_span_s=ts[-1]-ts[0],
          required_wall_span_s=seconds)
    check("no_producer_gap_over_one_second", order and all(
          b-a <= MAX_FRESHNESS_S for a, b in zip(ts, ts[1:])))
    report.update(unique_samples=len(unique), session_identity=list(identities)[0],
                  measurement_complete=complete, window_start_monotonic_s=start,
                  window_end_monotonic_s=finish)

    def sim_at(wall):
        index = bisect.bisect_right(ts, wall) - 1
        if index >= len(unique)-1:
            return unique[-1]["sim_time_ns"]*1e-9
        a, b = unique[index], unique[index+1]
        ratio = (wall-ts[index])/(ts[index+1]-ts[index])
        return (a["sim_time_ns"] + ratio*(b["sim_time_ns"]-a["sim_time_ns"]))*1e-9

    if complete:
        sim_start, sim_finish = sim_at(start), sim_at(finish)
        report.update(window_start_sim_time_s=sim_start, window_end_sim_time_s=sim_finish)
        sim_elapsed = sim_finish-sim_start
        rate = sim_elapsed/seconds
        report.update(sim_elapsed_s=sim_elapsed, wall_elapsed_s=seconds,
                      measured_realtime_factor=rate)
        check("overall_rate_0p98_to_1p02", RATE_MIN <= rate <= RATE_MAX, factor=rate)
        for index in range(int(seconds // WINDOW_S)):
            a, b = start+index*WINDOW_S, start+(index+1)*WINDOW_S
            value = (sim_at(b)-sim_at(a))/WINDOW_S
            report["windows"].append({"start_seconds": index*WINDOW_S,
                                      "duration_seconds": WINDOW_S, "factor": value})
        check("all_ten_second_windows_0p98_to_1p02", all(
              RATE_MIN <= w["factor"] <= RATE_MAX for w in report["windows"]))
        rolling_offsets = [index*ROLLING_STRIDE_S for index in
                           range(int(math.floor((seconds-WINDOW_S)/ROLLING_STRIDE_S))+1)]
        if not rolling_offsets or rolling_offsets[-1] < seconds-WINDOW_S:
            rolling_offsets.append(seconds-WINDOW_S)
        for offset in rolling_offsets:
            value = (sim_at(start+offset+WINDOW_S)-sim_at(start+offset))/WINDOW_S
            report["rolling_windows"].append({"start_seconds": offset,
                "duration_seconds": WINDOW_S, "factor": value})
        check("all_rolling_ten_second_windows_0p98_to_1p02",
              bool(report["rolling_windows"]) and all(
              RATE_MIN <= w["factor"] <= RATE_MAX for w in report["rolling_windows"]),
              stride_seconds=ROLLING_STRIDE_S, windows=len(report["rolling_windows"]))

    rendering = [r.get("rendering") or {} for r in unique]
    render_schema = all(all(finite(r.get(k)) for k in
                        ("scene_rendered_frames", "target_scene_hz", "scene_render_hz"))
                        and isinstance(r.get("renderer_enabled"), bool) for r in rendering)
    check("native_scene_draw_telemetry_present", diagnostic or render_schema,
          required_fields=["scene_rendered_frames", "target_scene_hz", "renderer_enabled", "scene_render_hz"])
    report["rendering"] = {"measurement": "native scene Draw calls, not display scanout",
                           "telemetry_present": render_schema, "windows": [],
                           "rolling_windows": [], "rolling_window_stride_seconds": ROLLING_STRIDE_S}
    if render_schema:
        monotonic = all(b["scene_rendered_frames"] >= a["scene_rendered_frames"]
                        for a, b in zip(rendering, rendering[1:]))
        check("native_scene_draw_counter_never_regresses", monotonic)
        check("native_renderer_enabled_with_60hz_target", diagnostic or all(
              r["renderer_enabled"] and abs(r["target_scene_hz"]-60.) < .001 for r in rendering))
        if complete:
            def draws_at(wall):
                index = bisect.bisect_right(ts, wall)-1
                if index >= len(rendering)-1:
                    return rendering[-1]["scene_rendered_frames"]
                a, b = rendering[index], rendering[index+1]
                fraction = (wall-ts[index])/(ts[index+1]-ts[index])
                return a["scene_rendered_frames"]+fraction*(b["scene_rendered_frames"]-a["scene_rendered_frames"])
            count = draws_at(finish)-draws_at(start)
            hz = count/seconds
            report["rendering"].update(scene_draws=count, observed_scene_hz=hz,
                                       target_hz=60., relative_tolerance=.05)
            check("native_scene_draw_rate_60hz", diagnostic or 57. <= hz <= 63.,
                  measured_scene_hz=hz, minimum_hz=57., maximum_hz=63.)
            for index in range(int(seconds // WINDOW_S)):
                a, b = start+index*WINDOW_S, start+(index+1)*WINDOW_S
                value = (draws_at(b)-draws_at(a))/WINDOW_S
                report["rendering"]["windows"].append({"start_seconds": index*WINDOW_S,
                    "duration_seconds": WINDOW_S, "observed_scene_hz": value})
            check("all_ten_second_scene_draw_windows_60hz", diagnostic or all(
                  57. <= w["observed_scene_hz"] <= 63. for w in report["rendering"]["windows"]))
            for offset in rolling_offsets:
                value = (draws_at(start+offset+WINDOW_S)-draws_at(start+offset))/WINDOW_S
                report["rendering"]["rolling_windows"].append({"start_seconds": offset,
                    "duration_seconds": WINDOW_S, "observed_scene_hz": value})
            check("all_rolling_ten_second_scene_draw_windows_60hz", diagnostic or (
                  bool(report["rendering"]["rolling_windows"]) and all(
                  57. <= w["observed_scene_hz"] <= 63.
                  for w in report["rendering"]["rolling_windows"])),
                  stride_seconds=ROLLING_STRIDE_S, windows=len(report["rendering"]["rolling_windows"]))

    first, last = unique[0], unique[-1]
    sensor_seconds = (last["sim_time_ns"]-first["sim_time_ns"])*1e-9
    check("sensor_counter_span_positive", sensor_seconds > 0)
    imu_ids = [{s.get("id") for s in (r.get("imu_stats") or [])} for r in unique]
    check("imu_sensor_set_present_and_constant", bool(imu_ids[0]) and all(isinstance(s, str) and s for s in imu_ids[0]) and
          all(s == imu_ids[0] for s in imu_ids))
    sensor_rates = {"imu": {}, "lidar": {}}
    report["sensor_rates"] = sensor_rates
    if sensor_seconds > 0:
        for sensor_id in sorted(x for x in imu_ids[0] if isinstance(x, str)):
            series = [next((s for s in (r.get("imu_stats") or []) if s.get("id") == sensor_id), {})
                      for r in unique]
            valid = all(all(finite(s.get(k)) for k in IMU_COUNTERS) for s in series)
            check("imu_counter_schema_"+sensor_id, valid)
            if not valid:
                continue
            increments = {k: series[-1][k]-series[0][k] for k in IMU_COUNTERS}
            rate = increments["measured_samples"]/sensor_seconds
            sensor_rates["imu"][sensor_id] = {"measured_hz": rate, "increments": increments}
            check("imu_sampling_rate_"+sensor_id, abs(rate/expected_imu_hz-1) <= .005, measured_hz=rate)
            check("imu_no_loss_or_reset_"+sensor_id, all(increments[k] == 0 for k in IMU_COUNTERS[1:])
                  and all(b[k] >= a[k] for a, b in zip(series, series[1:]) for k in IMU_COUNTERS))
        lidar = [r.get("lidar_stats") or {} for r in unique]
        valid = all(all(finite(s.get(k)) for k in LIDAR_COUNTERS) for s in lidar)
        check("lidar_counter_schema", valid)
        if valid:
            increments = {k: lidar[-1][k]-lidar[0][k] for k in LIDAR_COUNTERS}
            ray_rate = increments["traced_rays"]/sensor_seconds
            scan_rate = increments["published_scans"]/sensor_seconds
            sensor_rates["lidar"] = {"rays_per_second": ray_rate, "scans_per_second": scan_rate,
                                     "increments": increments}
            check("lidar_ray_budget_preserved", abs(ray_rate/expected_rays_per_second-1) <= .005,
                  measured_rays_per_second=ray_rate)
            check("lidar_scan_cadence_preserved", abs(scan_rate/expected_scan_hz-1) <= .02,
                  measured_scan_hz=scan_rate)
            check("lidar_no_loss_or_reset", increments["dropped_scans"] == 0
                  and increments["history_resets"] == 0 and
                  all(b[k] >= a[k] for a, b in zip(lidar, lidar[1:]) for k in LIDAR_COUNTERS))
    report["status"] = "passed" if all(c["passed"] for c in checks) else ("failed" if complete else "incomplete")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=60.)
    parser.add_argument("--warmup-seconds", type=float, default=10.)
    parser.add_argument("--ready-timeout-seconds", type=float, default=180.)
    parser.add_argument("--diagnostic-sensor-budget", action="store_true",
                        help="Diagnostic report only; permits explicitly declared non-production sensor budgets")
    parser.add_argument("--expected-rays-per-second", type=float, default=200000.)
    parser.add_argument("--expected-imu-hz", type=float, default=200.)
    parser.add_argument("--expected-scan-hz", type=float, default=10.)
    args = parser.parse_args()
    if not 10 <= args.seconds <= 600 or not 0 <= args.warmup_seconds <= 120 or not 1 <= args.ready_timeout_seconds <= 600:
        parser.error("Invalid measurement, warmup or readiness duration")
    budgets = (args.expected_rays_per_second, args.expected_imu_hz, args.expected_scan_hz)
    if not all(math.isfinite(x) and x > 0 for x in budgets):
        parser.error("Sensor budgets must be finite and positive")
    if not args.diagnostic_sensor_budget and (args.seconds < 60 or budgets != (200000., 200., 10.)):
        parser.error("Production acceptance requires at least 60 seconds, 200000 rays/s, 200 Hz IMU and 10 Hz scans; use explicit diagnostic mode for other budgets")
    args.output.mkdir(parents=True, exist_ok=False)
    rows, warmup_rows = [], []
    observer_started = time.monotonic()
    observer_started_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    def save_measurement_state(phase, **details):
        state = {"phase": phase, "session_directory": str(args.session.resolve()),
                 "observer_started_monotonic_s": observer_started,
                 "observer_started_utc": observer_started_utc,
                 "updated_monotonic_s": time.monotonic(),
                 "updated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 "requested_wall_seconds": args.seconds, **details}
        temporary = args.output/"measurement-state.json.tmp"
        temporary.write_text(json.dumps(state, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        temporary.replace(args.output/"measurement-state.json")
        print("CLOCK_MEASUREMENT_STATE "+json.dumps(state, allow_nan=False), flush=True)
    save_measurement_state("waiting_for_warmup", warmup_seconds=args.warmup_seconds)
    deadline = observer_started+args.ready_timeout_seconds
    warmup_start = None
    warmup_identity = None
    while time.monotonic() < deadline:
        row = read_observation(args.session)
        warmup_rows.append(row)
        identity = (row.get("session"), row.get("epoch"))
        if ready(row):
            if warmup_start is None or identity != warmup_identity:
                warmup_start, warmup_identity = time.monotonic(), identity
            if time.monotonic()-warmup_start >= args.warmup_seconds:
                rows.append(row)
                break
        else:
            warmup_start = None
        time.sleep(.1)
    if rows:
        finish = rows[0]["bridge_received_monotonic_s"] + args.seconds
        save_measurement_state("measuring",
            session_identity=[rows[0]["session"], rows[0]["epoch"]],
            window_start_monotonic_s=rows[0]["bridge_received_monotonic_s"],
            window_end_monotonic_s=finish, window_start_sim_time_s=rows[0]["sim_time_ns"]*1.e-9)
        # Bounded tail is for a producer timestamp bracketing the exact endpoint.
        while time.monotonic() <= finish + MAX_FRESHNESS_S:
            row = read_observation(args.session)
            rows.append(row)
            if finite(row.get("bridge_received_monotonic_s")) and row["bridge_received_monotonic_s"] >= finish:
                break
            time.sleep(.1)
    report = analyze_observations(rows, args.seconds, *budgets, diagnostic=args.diagnostic_sensor_budget)
    report.update(created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  session_directory=str(args.session.resolve()), command=[sys.executable, *sys.argv],
                  script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  warmup_seconds=args.warmup_seconds,
                  observer_started_monotonic_s=observer_started,
                  observer_started_utc=observer_started_utc,
                  warmup_completed=bool(rows),
                  scope="read-only UE/ROS producer telemetry; no control messages or truth injection")
    provenance = args.session / "runtime-provenance.json"
    if provenance.is_file():
        report["runtime_provenance_sha256"] = hashlib.sha256(provenance.read_bytes()).hexdigest()
    for name, data in (("observations.json", rows), ("warmup-observations.json", warmup_rows), ("verification.json", report)):
        (args.output/name).write_text(json.dumps(data, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    save_measurement_state("completed", status=report["status"],
                           verification_path=str((args.output/"verification.json").resolve()),
                           verification_sha256=hashlib.sha256((args.output/"verification.json").read_bytes()).hexdigest())
    print(json.dumps(report, indent=2, allow_nan=False), flush=True)
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
