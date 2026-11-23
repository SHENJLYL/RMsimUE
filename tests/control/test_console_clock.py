"""Guard measurement against the regressions and masked failures seen in native runs."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"tools/integration"))
from measure_console_clock import analyze_observations


def samples(sim_clock=lambda seconds: seconds, rays=200000, imu_hz=200, scans=10, scene_hz=60):
    result = []
    for index in range(122):
        elapsed = index*.5
        sim = 10.+sim_clock(elapsed)
        result.append({
            "observed_monotonic_s": 100.+elapsed+.2,
            "snapshot_written_monotonic_s": 100.+elapsed+.1,
            "bridge_received_monotonic_s": 100.+elapsed,
            "snapshot_sequence": index,
            "phase": "ready", "navigation_phase": "running",
            "session": "one-session", "epoch": 0,
            "sim_time_ns": round(sim*1e9), "connected": True, "state_fresh": True,
            "viewport_hz": 60., "pace_real_time": True, "no_rhi_diagnostic": False,
            "rendering": {"renderer_enabled": True, "target_scene_hz": 60.,
                          "scene_render_hz": 60.,
                          "scene_rendered_frames": 100+round(elapsed*scene_hz)},
            "imu_stats": [{"id": "mounted_imu", "measured_samples": round(sim*imu_hz),
                           "missed_periods": 0, "dropped_samples": 0,
                           "history_resets": 0, "invalid_samples": 0}],
            "lidar_stats": {"traced_rays": round(sim*rays),
                            "published_scans": round(sim*scans),
                            "dropped_scans": 0, "history_resets": 0},
        })
    return result


class ConsoleClockTests(unittest.TestCase):
    def assert_failed_check(self, report, name):
        self.assertNotEqual(report["status"], "passed")
        self.assertTrue(any(c["name"] == name and not c["passed"] for c in report["checks"]))

    def test_exact_one_x_with_duplicate_file_snapshots_passes(self):
        rows = []
        for row in samples():
            rows.append(row)
            duplicate = copy.deepcopy(row)
            duplicate["observed_monotonic_s"] += .1
            rows.append(duplicate)
        report = analyze_observations(rows)
        self.assertEqual(report["status"], "passed")
        self.assertAlmostEqual(report["measured_realtime_factor"], 1.)
        self.assertEqual(len(report["windows"]), 6)

    def test_mean_one_x_does_not_hide_slow_and_fast_ten_second_windows(self):
        def clock(t):
            if t <= 10: return t*.95
            if t <= 20: return 9.5+(t-10)*1.05
            return t
        report = analyze_observations(samples(clock))
        self.assertAlmostEqual(report["measured_realtime_factor"], 1.)
        self.assert_failed_check(report, "all_ten_second_windows_0p98_to_1p02")

    def test_shifted_slow_and_fast_periods_fail_rolling_clock_window(self):
        def clock(t):
            if t <= 5: return .9*t
            if t <= 15: return 4.5+1.1*(t-5)
            if t <= 20: return 15.5+.9*(t-15)
            return t
        report = analyze_observations(samples(clock))
        self.assertAlmostEqual(report["measured_realtime_factor"], 1.)
        self.assertTrue(all(.98 <= w["factor"] <= 1.02 for w in report["windows"]))
        self.assertEqual(len(report["rolling_windows"]), 51)
        self.assert_failed_check(report, "all_rolling_ten_second_windows_0p98_to_1p02")

    def test_shifted_scene_draw_stalls_fail_rolling_window(self):
        rows = samples()
        for row in rows:
            t = row["bridge_received_monotonic_s"]-100.
            count = 50*t if t <= 5 else (250+70*(t-5) if t <= 15
                    else (950+50*(t-15) if t <= 20 else 60*t))
            row["rendering"]["scene_rendered_frames"] = 100+count
        report = analyze_observations(rows)
        self.assertAlmostEqual(report["rendering"]["observed_scene_hz"], 60.)
        self.assertTrue(all(57. <= w["observed_scene_hz"] <= 63.
                            for w in report["rendering"]["windows"]))
        self.assertEqual(len(report["rendering"]["rolling_windows"]), 51)
        self.assert_failed_check(report, "all_rolling_ten_second_scene_draw_windows_60hz")

    def test_backward_clock_is_not_discarded_as_a_duplicate(self):
        rows = samples()
        rows[30]["sim_time_ns"] = rows[29]["sim_time_ns"]-1
        self.assert_failed_check(analyze_observations(rows), "sim_clock_never_regresses")

    def test_epoch_reset_is_not_hidden_by_later_recovery(self):
        rows = samples()
        rows[35]["epoch"] = 1
        self.assert_failed_check(analyze_observations(rows), "session_identity_constant")

    def test_old_but_repeatedly_read_snapshot_is_stale(self):
        rows = samples()
        rows[40]["snapshot_written_monotonic_s"] -= 2
        self.assert_failed_check(analyze_observations(rows), "ready_and_fresh_for_entire_window")

    def test_short_valid_prefix_does_not_pass_full_window(self):
        report = analyze_observations(samples()[:80])
        self.assertEqual(report["status"], "incomplete")
        self.assert_failed_check(report, "complete_requested_window")

    def test_missing_metadata_in_middle_is_not_silently_dropped(self):
        rows = samples()
        del rows[25]["bridge_received_monotonic_s"]
        self.assert_failed_check(analyze_observations(rows), "every_decoded_snapshot_has_clock_metadata")

    def test_imu_loss_and_accumulating_scan_delay_fail(self):
        rows = samples(scans=9.53)
        for row in rows[60:]:
            row["imu_stats"][0]["dropped_samples"] = 1
        report = analyze_observations(rows)
        self.assert_failed_check(report, "imu_no_loss_or_reset_mounted_imu")
        self.assert_failed_check(report, "lidar_scan_cadence_preserved")

    def test_scene_draw_five_percent_boundaries_are_inclusive(self):
        for hz in (57., 63.):
            with self.subTest(scene_hz=hz):
                self.assertEqual(analyze_observations(samples(scene_hz=hz))["status"], "passed")

    def test_reported_sixty_hz_cannot_override_a_fifty_hz_native_counter(self):
        report = analyze_observations(samples(scene_hz=50))
        self.assert_failed_check(report, "native_scene_draw_rate_60hz")
        self.assertAlmostEqual(report["rendering"]["observed_scene_hz"], 50.)

    def test_scene_draw_stalls_fail_even_when_total_count_is_sixty_hz(self):
        rows = samples()
        for row in rows:
            t = row["bridge_received_monotonic_s"]-100.
            count = 50*t if t <= 10 else (500+70*(t-10) if t <= 20 else 60*t)
            row["rendering"]["scene_rendered_frames"] = 100+count
        report = analyze_observations(rows)
        self.assertAlmostEqual(report["rendering"]["observed_scene_hz"], 60.)
        self.assert_failed_check(report, "all_ten_second_scene_draw_windows_60hz")

    def test_missing_render_data_and_counter_resets_cannot_pass_production(self):
        rows = samples()
        del rows[20]["rendering"]
        self.assert_failed_check(analyze_observations(rows), "native_scene_draw_telemetry_present")
        rows = samples()
        rows[30]["rendering"]["scene_rendered_frames"] = 0
        self.assert_failed_check(analyze_observations(rows), "native_scene_draw_counter_never_regresses")

    def test_lower_ray_budget_is_only_eligible_for_explicit_diagnostic_report(self):
        rows = samples(rays=75000)
        self.assert_failed_check(analyze_observations(rows), "lidar_ray_budget_preserved")
        self.assert_failed_check(analyze_observations(rows, expected_rays_per_second=75000),
                                 "production_measurement_contract")
        diagnostic = analyze_observations(rows, expected_rays_per_second=75000, diagnostic=True)
        self.assertEqual(diagnostic["status"], "passed")
        self.assertFalse(diagnostic["production_eligible"])
        self.assertEqual(diagnostic["acceptance_scope"], "diagnostic_realtime")


if __name__ == "__main__":
    unittest.main()
