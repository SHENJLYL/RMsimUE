"""Display scheduling and truthful presentation evidence, without any GL window."""
import contextlib
import ctypes
import io
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live_viewer as viewer


def snapshot(sequence, generation=1, sim_time=1.0):
    return SimpleNamespace(sequence=sequence, generation=generation, sim_time_s=sim_time)


class RenderScheduleTests(unittest.TestCase):
    def test_unchanged_ready_state_has_no_periodic_geometry_redraw(self):
        schedule = viewer.DrawSchedule(30)
        self.assertTrue(schedule.begin(0.0, "waiting", False))
        schedule.request("native_snapshot")
        self.assertTrue(schedule.begin(0.04, "ready", False))
        for now in (0.08, 0.5, 1.5, 30.0):
            self.assertEqual(schedule.begin(now, "ready", False), ())
            self.assertGreater(schedule.wait_seconds(now, "ready", False), 0)

    def test_camera_native_resize_refresh_coalesce_and_keep_30hz_cap(self):
        schedule = viewer.DrawSchedule(30)
        schedule.begin(0.0, "ready", False)
        for reason in ("camera", "native_snapshot", "resize", "refresh"):
            schedule.request(reason)
        self.assertFalse(schedule.begin(0.01, "ready", False))
        reasons = schedule.begin(1.0 / 30, "ready", False)
        self.assertEqual(set(reasons), {"camera", "native_snapshot", "resize", "refresh"})
        self.assertFalse(schedule.begin(0.05, "ready", False))

    def test_waiting_and_stale_heartbeat_is_one_hz(self):
        for phase in ("waiting", "stale"):
            schedule = viewer.DrawSchedule(30)
            self.assertTrue(schedule.begin(0.0, phase, False))
            self.assertFalse(schedule.begin(0.9, phase, False))
            self.assertEqual(schedule.begin(1.0, phase, False), ("idle_status",))
            schedule.request("camera")
            self.assertEqual(schedule.begin(1.04, phase, False), ("camera",))

    def test_minimize_keeps_latest_work_and_restores_without_spin(self):
        schedule = viewer.DrawSchedule(30)
        schedule.begin(0.0, "ready", False)
        schedule.request("native_snapshot")
        schedule.request("window_state")
        for now in (0.1, 1.0, 100.0):
            self.assertFalse(schedule.begin(now, "ready", True))
            self.assertEqual(schedule.wait_seconds(now, "ready", True), 0.02)
        self.assertEqual(set(schedule.begin(100.02, "ready", False)), {"native_snapshot", "window_state"})

    def test_continuous_baseline_renders_same_state_but_obeys_rate(self):
        schedule = viewer.DrawSchedule(30, continuous=True)
        self.assertIn("continuous_diagnostic", schedule.begin(0.0, "ready", False))
        self.assertFalse(schedule.begin(0.02, "ready", False))
        self.assertEqual(schedule.begin(1.0 / 30, "ready", False), ("continuous_diagnostic",))
        self.assertFalse(schedule.begin(1.0, "ready", True))

    def test_packet_received_during_slow_draw_is_pending_without_extra_idle(self):
        schedule = viewer.DrawSchedule(30)
        self.assertTrue(schedule.begin(0.0, "ready", False))
        applied, newer = snapshot(1), snapshot(2)
        receiver = SimpleNamespace(read=lambda: ((newer, 0.02), {"receiver_error": None}))
        latest, _ = viewer.recheck_after_draw(receiver, applied, schedule)
        self.assertIs(latest[0], newer)
        self.assertEqual(schedule.wait_seconds(0.04, "ready", False), 0.0)
        self.assertEqual(schedule.begin(0.04, "ready", False), ("native_snapshot",))

    def test_packet_received_during_fast_draw_still_obeys_30hz_cap(self):
        schedule = viewer.DrawSchedule(30)
        schedule.begin(0.0, "ready", False)
        receiver = SimpleNamespace(read=lambda: ((snapshot(2), 0.005), {"receiver_error": None}))
        viewer.recheck_after_draw(receiver, snapshot(1), schedule)
        self.assertFalse(schedule.begin(0.01, "ready", False))
        self.assertGreater(schedule.wait_seconds(0.01, "ready", False), 0.0)
        self.assertTrue(schedule.begin(1.0 / 30, "ready", False))

    def test_post_draw_recheck_does_not_repeat_unchanged_state(self):
        schedule = viewer.DrawSchedule(30)
        schedule.begin(0.0, "ready", False)
        source = snapshot(1)
        receiver = SimpleNamespace(read=lambda: ((source, 0.0), {"receiver_error": None}))
        viewer.recheck_after_draw(receiver, source, schedule)
        self.assertFalse(schedule.begin(0.05, "ready", False))
        self.assertEqual(schedule.wait_seconds(0.05, "ready", False), 0.02)


class ReceiverWakeTests(unittest.TestCase):
    """Real loopback UDP and receiver thread; callback stands in for GLFW only."""

    def setUp(self):
        self.contract = viewer.ViewerContract("a" * 32, "b" * 64, "c" * 64, 21, 20, 0, 14, 16)
        self.receiver = viewer.SnapshotReceiver(0, self.contract)
        self.addCleanup(self.receiver.close)
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.sender.close)
        self.address = self.receiver.socket.getsockname()

    def send(self, sequence, generation=1, sim_time=1.0, **overrides):
        from viewer_protocol import STATE_SCHEMA
        payload = dict(schema=STATE_SCHEMA, session_token="a" * 32,
                       model_sha256="b" * 64, physics_identity_sha256="c" * 64,
                       nq=21, nv=20, na=0, nu=14, nbody=16,
                       generation=generation, sequence=sequence, sim_time_s=sim_time,
                       qpos=[0.0] * 21, qvel=[0.0] * 20, act=[], ctrl=[0.0] * 14,
                       body_xpos=[[0.0, 0.0, 0.0] for _ in range(16)],
                       body_xquat=[[1.0, 0.0, 0.0, 0.0] for _ in range(16)])
        payload.update(overrides)
        self.sender.sendto(json.dumps(payload).encode(), self.address)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.002)
        self.fail("receiver condition did not complete")

    def test_only_accepted_snapshots_wake_between_registration_and_teardown(self):
        wakes = []
        self.send(1)
        self.wait_for(lambda: self.receiver.read()[1]["accepted_snapshots"] == 1)
        self.assertEqual(self.receiver.read()[1]["snapshot_wake_notifications"], 0)
        self.receiver.set_wake(lambda: wakes.append(threading.current_thread().name))
        self.send(2, session_token="d" * 32)
        self.wait_for(lambda: bool(self.receiver.read()[1]["rejected_snapshots"]))
        self.assertEqual(wakes, [])
        self.send(2)
        self.wait_for(lambda: self.receiver.read()[1]["snapshot_wake_notifications"] == 1)
        self.assertEqual(wakes, ["viewer-snapshots"])
        # Epoch restart remains an accepted display update and wakes the same loop.
        self.send(3, generation=2, sim_time=0.005)
        self.wait_for(lambda: self.receiver.read()[1]["snapshot_wake_notifications"] == 2)
        self.assertEqual(self.receiver.read()[0][0].generation, 2)
        self.receiver.set_wake(None)
        self.send(4, generation=2, sim_time=0.01)
        self.wait_for(lambda: self.receiver.read()[1]["accepted_snapshots"] == 4)
        self.assertEqual(len(wakes), 2)
        self.receiver.close()
        self.assertFalse(self.receiver.thread.is_alive())
        with self.assertRaisesRegex(RuntimeError, "closed snapshot receiver"):
            self.receiver.set_wake(lambda: None)

    def test_close_drains_inflight_wake_before_returning_for_glfw_teardown(self):
        entered, release, close_started, closed = (threading.Event() for _ in range(4))
        order = []

        def wake():
            entered.set()
            self.assertTrue(release.wait(2))
            order.append("wake_completed")

        def close():
            close_started.set()
            self.receiver.close()
            order.append("receiver_closed")
            closed.set()

        self.receiver.set_wake(wake)
        self.send(1)
        self.assertTrue(entered.wait(2))
        closer = threading.Thread(target=close)
        closer.start()
        try:
            self.assertTrue(close_started.wait(2))
            self.assertFalse(closed.is_set())
        finally:
            release.set()
            closer.join(2)
        self.assertFalse(closer.is_alive())
        self.assertTrue(closed.is_set())
        self.assertEqual(order, ["wake_completed", "receiver_closed"])
        self.assertFalse(self.receiver.thread.is_alive())

    def test_sequence_gap_is_distinct_from_render_coalescing(self):
        self.send(1)
        self.send(2)
        self.send(4)
        self.wait_for(lambda: self.receiver.read()[1]["accepted_snapshots"] == 3)
        latest, report = self.receiver.read()
        self.assertEqual(latest[0].sequence, 4)
        self.assertEqual(report["unreceived_snapshot_sequences"], 1)
        self.assertEqual(report["rejected_snapshots"], {})

    def test_callback_failure_is_reported_and_receiver_still_closes(self):
        def failed_wake():
            raise RuntimeError("wake fixture error")
        self.receiver.set_wake(failed_wake)
        self.send(1)
        self.wait_for(lambda: self.receiver.read()[1]["receiver_error"] is not None)
        self.assertIn("wake fixture error", self.receiver.read()[1]["receiver_error"])
        self.receiver.close()
        self.assertFalse(self.receiver.thread.is_alive())


class PresentationEvidenceTests(unittest.TestCase):
    def test_camera_current_and_last_successful_render_are_independent_copies(self):
        camera = SimpleNamespace(type=1, lookat=[1.0, 2.0, 3.0], distance=3.5,
                                 azimuth=135.0, elevation=-25.0)
        drawn = viewer.camera_state(camera)
        presented = viewer.PresentedState()
        source = snapshot(1)
        self.assertIsNone(presented.fields(source, 0.0)["camera_rendered"])
        presented.swapped(source, {"position_m": 0.0}, 0.1, drawn)
        camera.lookat[0] = 99.0
        camera.azimuth = 90.0
        drawn["lookat"][1] = 88.0
        self.assertEqual(presented.fields(source, 0.2)["camera_rendered"], {
            "type": 1, "lookat": [1.0, 2.0, 3.0], "distance": 3.5,
            "azimuth": 135.0, "elevation": -25.0})
        self.assertEqual(viewer.camera_state(camera)["lookat"], [99.0, 2.0, 3.0])
        self.assertEqual(viewer.camera_state(camera)["azimuth"], 90.0)

    def test_first_ready_requires_native_swap_not_receipt_or_background_frame(self):
        presented = viewer.PresentedState()
        accepted = snapshot(10)
        self.assertEqual(presented.phase("ready"), "waiting")
        self.assertFalse(presented.fields(accepted, 0.0)["body_pose_matches"])
        presented.swapped(None, None, 0.1)
        self.assertEqual(presented.phase("ready"), "waiting")
        presented.swapped(accepted, {"position_m": 0.0, "orientation_rad": 0.0}, 0.2)
        self.assertEqual(presented.phase("ready"), "ready")
        self.assertEqual(presented.fields(accepted, 0.2)["rendered_sequence"], 10)

    def test_minimized_new_generation_never_claimed_as_rendered(self):
        presented = viewer.PresentedState()
        old = snapshot(10, 1, 25.0)
        new = snapshot(11, 2, 0.005)
        old_error = {"position_m": 1e-10, "orientation_rad": 0.0}
        presented.swapped(old, old_error, 1.0)
        paused_fields = presented.fields(new, 10.0)
        self.assertEqual(paused_fields["rendered_sequence"], 10)
        self.assertEqual(paused_fields["rendered_generation"], 1)
        self.assertEqual(paused_fields["rendered_sim_time_s"], 25.0)
        self.assertEqual(paused_fields["max_body_pose_error"], old_error)
        self.assertFalse(paused_fields["rendered_source_is_latest"])
        self.assertEqual(paused_fields["frames"], 1)
        presented.swapped(new, {"position_m": 0.0, "orientation_rad": 0.0}, 10.1)
        self.assertEqual(presented.fields(new, 10.1)["rendered_generation"], 2)
        self.assertTrue(presented.fields(new, 10.1)["rendered_source_is_latest"])

    def test_repeat_camera_frames_count_separately_and_source_stale_remains_visible(self):
        presented = viewer.PresentedState()
        source = snapshot(1)
        for now in (0.0, 0.1, 0.2):
            presented.swapped(source, {"position_m": 0.0}, now)
        fields = presented.fields(source, 2.0)
        self.assertEqual(fields["distinct_native_snapshot_frames"], 1)
        self.assertEqual(fields["repeated_native_snapshot_frames"], 2)
        self.assertEqual(presented.phase("stale"), "stale")


class RenderDiagnosticsTests(unittest.TestCase):
    def test_disabled_diagnostics_do_not_sample_timing_or_publish(self):
        with mock.patch.object(viewer.time, "monotonic", side_effect=AssertionError("diagnostic clock sampled")), \
             mock.patch.object(viewer.time, "process_time", side_effect=AssertionError("diagnostic CPU sampled")), \
             mock.patch.object(viewer.time, "perf_counter", side_effect=AssertionError("scope sampled")):
            diagnostic = viewer.RenderTiming(False)
            self.assertIsNone(diagnostic.start())
            diagnostic.stop("render_submit", None)
            diagnostic.idle(0.02, None)
            writer = mock.Mock()
            diagnostic.publish(writer, {}, final=True)
            writer.update.assert_not_called()

    def test_enabled_interval_has_separate_submit_swap_idle_wall_scopes(self):
        writer = mock.Mock()
        with mock.patch.object(viewer.time, "monotonic", side_effect=[10.0, 15.0]), \
             mock.patch.object(viewer.time, "process_time", side_effect=[2.0, 2.5]), \
             mock.patch.object(viewer.time, "perf_counter", side_effect=[20.0, 20.01, 21.0, 21.02, 22.0, 22.03]):
            diagnostic = viewer.RenderTiming(True)
            for name in ("render_submit", "swap", "idle"):
                diagnostic.stop(name, diagnostic.start())
            diagnostic.draw(("native_snapshot",))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                diagnostic.publish(writer, {"frames": 1})
        prefix, payload = output.getvalue().split(" ", 1)
        self.assertEqual(prefix, "MUJOCO_VIEWER_TIMING")
        report = json.loads(payload)
        self.assertEqual(report["schema"], "rmsim_mujoco_viewer_timing_v1")
        self.assertEqual(report["window_wall_s"], 5.0)
        self.assertEqual(report["process_cpu_one_core_percent"], 10.0)
        for name, expected in (("render_submit", 10), ("swap", 20), ("idle", 30)):
            self.assertAlmostEqual(report["scopes_ms"][name]["total"], expected)

    def test_requested_idle_and_actual_event_wait_are_reported_separately(self):
        writer = mock.Mock()
        with mock.patch.object(viewer.time, "monotonic", side_effect=[10.0, 15.0]), \
             mock.patch.object(viewer.time, "process_time", side_effect=[2.0, 2.1]), \
             mock.patch.object(viewer.time, "perf_counter", side_effect=[1.0, 1.004, 2.0, 2.017]):
            diagnostic = viewer.RenderTiming(True)
            diagnostic.idle(0.020, diagnostic.start())
            diagnostic.idle(0.015, diagnostic.start())
            with contextlib.redirect_stdout(io.StringIO()):
                diagnostic.publish(writer, {})
        report = writer.update.call_args.kwargs["render_timing"]
        self.assertEqual(report["idle_requested_ms"], {"count": 2, "total": 35.0, "mean": 17.5, "max": 20.0})
        self.assertAlmostEqual(report["scopes_ms"]["idle"]["total"], 21.0)
        self.assertEqual(report["scopes_ms"]["idle"]["count"], 2)
        self.assertEqual(diagnostic.idle_requested, [0, 0.0, 0.0])

    def test_gl_identity_reads_driver_strings_via_correct_ctypes_abi_without_gl_init(self):
        buffers = {0x1F00: ctypes.create_string_buffer(b"fixture vendor"),
                   0x1F01: ctypes.create_string_buffer(b"fixture renderer"),
                   0x1F02: ctypes.create_string_buffer(b"fixture version")}
        factory = ctypes.WINFUNCTYPE if os.name == "nt" else ctypes.CFUNCTYPE
        callback = factory(ctypes.c_void_p, ctypes.c_uint)(lambda enum: ctypes.addressof(buffers[enum]))
        glfw = SimpleNamespace(_glfw=SimpleNamespace(_name="fixture-glfw.dll"),
            CONTEXT_VERSION_MAJOR=1, CONTEXT_VERSION_MINOR=2, CONTEXT_CREATION_API=3,
            get_window_attrib=lambda window, key: {1: 4, 2: 6, 3: 0x00036001}[key],
            get_version_string=lambda: b"fixture GLFW",
            get_proc_address=lambda name: ctypes.cast(callback, ctypes.c_void_p).value)
        result = viewer.graphics_identity(glfw, object(), SimpleNamespace(windowSamples=0, windowDoublebuffer=1))
        self.assertEqual(result["vendor"], "fixture vendor")
        self.assertEqual(result["renderer"], "fixture renderer")
        self.assertEqual(result["version"], "fixture version")
        self.assertEqual(result["window_samples"], 0)
        self.assertTrue(result["window_doublebuffer"])


if __name__ == "__main__":
    unittest.main()
