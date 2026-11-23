"""Atomic clock-state publication preserves the production measurement window."""
from contextlib import contextmanager
import ctypes
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "clock_atomic_tested", ROOT / "tools/integration/measure_console_clock.py")
clock = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(clock)

PAYLOAD = (json.dumps({
    "phase": "measuring", "updated_monotonic_s": 230466.5736303,
    "window_start_monotonic_s": 230466.1487595,
    "window_end_monotonic_s": 230526.1487595,
    "window_start_sim_time_s": 20.02, "requested_wall_seconds": 60.0,
}, indent=2) + "\n").encode()
ORIGINAL = b'{"phase":"waiting_for_warmup"}\n'


def windows_error(code):
    error = PermissionError(errno.EACCES, "test destination handle")
    error.winerror = code
    return error


@contextmanager
def files():
    with tempfile.TemporaryDirectory(prefix="rmsim-clock-atomic-") as directory:
        target = Path(directory) / "measurement-state.json"
        temporary = target.with_suffix(".json.tmp")
        target.write_bytes(ORIGINAL)
        temporary.write_bytes(PAYLOAD)
        yield temporary, target


class DenyDeleteHandle:
    """A real Windows reader which permits reading/writing but denies rename."""
    def __init__(self, path):
        from ctypes import wintypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateFileW.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        self.kernel.CreateFileW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateFileW(
            str(path), 0x80000000, 0x00000003, None, 3, 0x80, None)
        if self.handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self.lock = threading.Lock()

    def close(self):
        with self.lock:
            if self.handle is not None:
                if not self.kernel.CloseHandle(self.handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                self.handle = None


class ClockAtomicTests(unittest.TestCase):
    def test_uncontended_replacement_is_atomic_and_keeps_original_timestamps(self):
        with files() as (temporary, target):
            with patch.object(clock.time, "sleep") as sleep:
                clock.replace_measurement_state(temporary, target)
            sleep.assert_not_called()
            self.assertEqual(target.read_bytes(), PAYLOAD)
            self.assertFalse(temporary.exists())

    def test_only_windows_5_and_32_get_short_retries(self):
        for code in (5, 32):
            with self.subTest(winerror=code), files() as (temporary, target):
                now = [10.]
                sleeps = []
                calls = []
                original_replace = Path.replace
                def replace(path, destination):
                    calls.append(path.read_bytes())
                    if len(calls) < 3:
                        raise windows_error(code)
                    return original_replace(path, destination)
                def sleep(delay):
                    sleeps.append(delay)
                    now[0] += delay
                fake_time = SimpleNamespace(monotonic=lambda: now[0], sleep=sleep)
                with patch.object(clock, "os", SimpleNamespace(name="nt")), \
                        patch.object(clock, "time", fake_time), \
                        patch.object(Path, "replace", replace):
                    clock.replace_measurement_state(temporary, target)
                self.assertEqual(sleeps, [.01, .01])
                self.assertEqual(calls, [PAYLOAD, PAYLOAD, PAYLOAD])
                self.assertEqual(target.read_bytes(), PAYLOAD)

    def test_permanent_windows_error_has_fixed_budget_and_preserves_both_files(self):
        with files() as (temporary, target):
            now = [0.]
            sleeps = []
            error = windows_error(5)
            def sleep(delay):
                sleeps.append(delay)
                now[0] += delay
            fake_time = SimpleNamespace(monotonic=lambda: now[0], sleep=sleep)
            with patch.object(clock, "os", SimpleNamespace(name="nt")), \
                    patch.object(clock, "time", fake_time), \
                    patch.object(Path, "replace", side_effect=error) as replace:
                with self.assertRaises(PermissionError) as raised:
                    clock.replace_measurement_state(temporary, target)
            self.assertIs(raised.exception, error)
            self.assertAlmostEqual(now[0], .2)
            self.assertAlmostEqual(sum(sleeps), .2)
            self.assertLessEqual(max(sleeps), .01)
            self.assertLessEqual(replace.call_count, 22)
            self.assertEqual(temporary.read_bytes(), PAYLOAD)
            self.assertEqual(target.read_bytes(), ORIGINAL)

    def test_other_errors_and_non_windows_errors_propagate_without_retry(self):
        for platform, code in (("nt", 87), ("nt", 2), ("posix", 5), ("posix", 32)):
            with self.subTest(platform=platform, winerror=code), files() as (temporary, target):
                error = windows_error(code)
                with patch.object(clock, "os", SimpleNamespace(name=platform)), \
                        patch.object(Path, "replace", side_effect=error) as replace, \
                        patch.object(clock.time, "sleep") as sleep:
                    with self.assertRaises(PermissionError) as raised:
                        clock.replace_measurement_state(temporary, target)
                self.assertIs(raised.exception, error)
                self.assertEqual(replace.call_count, 1)
                sleep.assert_not_called()
                self.assertEqual(temporary.read_bytes(), PAYLOAD)
                self.assertEqual(target.read_bytes(), ORIGINAL)

    def test_real_missing_parent_error_is_unchanged(self):
        with files() as (temporary, target):
            missing = target.parent / "absent" / target.name
            with patch.object(clock.time, "sleep") as sleep:
                with self.assertRaises(FileNotFoundError):
                    clock.replace_measurement_state(temporary, missing)
            sleep.assert_not_called()
            self.assertEqual(temporary.read_bytes(), PAYLOAD)
            self.assertEqual(target.read_bytes(), ORIGINAL)

    @unittest.skipUnless(os.name == "nt", "Requires real Windows share-delete semantics")
    def test_windows_reader_released_after_60ms_recovers(self):
        with files() as (temporary, target):
            holder = DenyDeleteHandle(target)
            release = threading.Event()
            thread = None
            try:
                with self.assertRaises(OSError) as initial:
                    temporary.replace(target)
                self.assertIn(initial.exception.winerror, (5, 32))
                thread = threading.Thread(target=lambda: (release.wait(.06), holder.close()))
                started = time.monotonic()
                thread.start()
                clock.replace_measurement_state(temporary, target)
                elapsed = time.monotonic() - started
                self.assertEqual(target.read_bytes(), PAYLOAD)
                self.assertFalse(temporary.exists())
                self.assertLess(elapsed, .25)
                print("CLOCK_ATOMIC_WINDOWS " + json.dumps({
                    "case": "reader_released", "initial_winerror": initial.exception.winerror,
                    "release_delay_s": .06, "elapsed_s": elapsed,
                    "payload_sha256": hashlib.sha256(PAYLOAD).hexdigest(),
                    "source_window_unchanged": True}), flush=True)
            finally:
                release.set()
                if thread is not None:
                    thread.join(1.)
                    self.assertFalse(thread.is_alive())
                holder.close()

    @unittest.skipUnless(os.name == "nt", "Requires real Windows share-delete semantics")
    def test_windows_reader_kept_open_fails_after_bounded_retry(self):
        with files() as (temporary, target):
            holder = DenyDeleteHandle(target)
            try:
                started = time.monotonic()
                with self.assertRaises(OSError) as raised:
                    clock.replace_measurement_state(temporary, target)
                elapsed = time.monotonic() - started
                self.assertIn(raised.exception.winerror, (5, 32))
                self.assertGreaterEqual(elapsed, .19)
                self.assertLess(elapsed, .25)
                self.assertEqual(temporary.read_bytes(), PAYLOAD)
                self.assertEqual(target.read_bytes(), ORIGINAL)
                print("CLOCK_ATOMIC_WINDOWS " + json.dumps({
                    "case": "reader_kept_open", "winerror": raised.exception.winerror,
                    "elapsed_s": elapsed, "temporary_preserved": True,
                    "destination_preserved": True}), flush=True)
            finally:
                holder.close()


if __name__ == "__main__":
    unittest.main()
