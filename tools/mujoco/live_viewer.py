"""Read-only MuJoCo rendering of the authoritative RMSim UE solver.

Uses the public low-level visualizer and GLFW.  Physics is never advanced here;
the complete sealed model is a display mirror.  UDP is receive-only and camera
interaction has no connection to vehicle commands, model controls or resets.
"""
from __future__ import annotations

import argparse
from collections import Counter
import ctypes
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import sys
import threading
import time
from typing import Any, Callable

from viewer_protocol import (
    MAX_PACKET_BYTES, SNAPSHOT_FIELDS, STATUS_SCHEMA,
    Snapshot, SnapshotError, SnapshotValidator, ViewerContract, compare_body_poses,
    normalize_session_token,
)


POSITION_TOLERANCE_M = 1e-8
ORIENTATION_TOLERANCE_RAD = 1e-7
STATUS_PERIOD_S = 0.25
STALE_AFTER_S = 0.75
IDLE_REDRAW_PERIOD_S = 1.0
TIMING_PERIOD_S = 5.0


class FullscreenCoverage:
    """Skip display work only behind the owned, opaque foreground UE popup."""

    def __init__(self, pid_file: Path, viewer_hwnd: int):
        from ctypes import wintypes as w

        self.pid_file = pid_file
        self.viewer_hwnd = viewer_hwnd
        self.ue_pid = None
        user = ctypes.WinDLL("user32", use_last_error=True)
        self.foreground = user.GetForegroundWindow
        self.foreground.argtypes = []
        self.foreground.restype = w.HWND
        self.process = user.GetWindowThreadProcessId
        self.process.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.process.restype = w.DWORD
        self.style = user.GetWindowLongW
        self.style.argtypes = [w.HWND, ctypes.c_int]
        self.style.restype = w.LONG
        self.minimized = user.IsIconic
        self.minimized.argtypes = [w.HWND]
        self.minimized.restype = w.BOOL
        self.region = user.GetWindowRgnBox
        self.region.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
        self.region.restype = ctypes.c_int
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        self.attribute = dwm.DwmGetWindowAttribute
        self.attribute.argtypes = [w.HWND, w.DWORD, ctypes.c_void_p, w.DWORD]
        self.attribute.restype = ctypes.c_long
        self.rect = w.RECT
        self.dword = w.DWORD

    def covered(self) -> bool:
        if self.ue_pid is None:
            try:
                pid = int(self.pid_file.read_text(encoding="ascii"))
            except (OSError, ValueError):
                return False
            if pid <= 0:
                return False
            self.ue_pid = pid
        window = self.foreground()
        if not window or window == self.viewer_hwnd or self.minimized(window):
            return False
        if self.style(self.viewer_hwnd, -20) & 0x00000008:  # WS_EX_TOPMOST
            return False
        pid = self.dword()
        if not self.process(window, ctypes.byref(pid)) or pid.value != self.ue_pid:
            return False
        style, extended = self.style(window, -16), self.style(window, -20)
        # WS_POPUP, no WS_CAPTION, WS_EX_LAYERED or WS_EX_TRANSPARENT.
        if not style & 0x80000000 or style & 0x00C00000 or extended & 0x00080020:
            return False
        if self.region(window, ctypes.byref(self.rect())):
            return False  # Explicit non-default window region. Fail open.
        cloaked = self.dword()
        if self.attribute(window, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) or cloaked.value:
            return False
        outer, inner = self.rect(), self.rect()
        for handle, rect in ((window, outer), (self.viewer_hwnd, inner)):
            if self.attribute(handle, 9, ctypes.byref(rect), ctypes.sizeof(rect)):
                return False
        return (outer.left <= inner.left < inner.right <= outer.right and
                outer.top <= inner.top < inner.bottom <= outer.bottom)


class DrawSchedule:
    """Coalesce display changes without advancing or predicting source state."""

    def __init__(self, max_fps: float, continuous: bool = False):
        self.period = 1.0 / max_fps
        self.continuous = continuous
        self.pending = {"initial"}
        self.next_frame = 0.0
        self.next_idle_frame = 0.0

    def request(self, reason: str) -> None:
        self.pending.add(reason)

    def begin(self, now: float, source_phase: str, paused: bool) -> tuple[str, ...]:
        if paused or now < self.next_frame:
            return ()
        reasons = set(self.pending)
        if self.continuous:
            reasons.add("continuous_diagnostic")
        elif source_phase in ("waiting", "stale") and now >= self.next_idle_frame:
            reasons.add("idle_status")
        if not reasons:
            return ()
        self.pending.clear()
        self.next_frame = now + self.period
        self.next_idle_frame = now + IDLE_REDRAW_PERIOD_S
        return tuple(sorted(reasons))

    def wait_seconds(self, now: float, source_phase: str, paused: bool) -> float:
        # A bounded event wait also services new UDP state, parent exit and stale
        # transitions while there is no display work. Never spin when minimized.
        if paused:
            return 0.02
        if self.pending or self.continuous:
            deadline = self.next_frame
        elif source_phase in ("waiting", "stale"):
            deadline = self.next_idle_frame
        else:
            return 0.02
        return min(0.02, max(0.0, deadline - now))


class PresentedState:
    """Published display evidence changes only after a successful buffer swap."""

    def __init__(self):
        self.frames = 0
        self.native_frames = 0
        self.distinct_native_frames = 0
        self.snapshot: Snapshot | None = None
        self.pose_error: dict[str, float] | None = None
        self.last_render_at: float | None = None
        self.camera: dict | None = None

    def swapped(self, snapshot: Snapshot | None, pose_error: dict | None, now: float,
                camera: dict | None = None) -> None:
        if snapshot is not None and pose_error is None:
            raise ValueError("a native display frame requires its body pose comparison")
        self.frames += 1
        self.last_render_at = now
        if camera is not None:
            self.camera = {name: list(value) if isinstance(value, list) else value
                           for name, value in camera.items()}
        if snapshot is not None:
            if self.snapshot is None or self.snapshot.sequence != snapshot.sequence:
                self.distinct_native_frames += 1
            self.snapshot = snapshot
            self.pose_error = dict(pose_error)
            self.native_frames += 1

    def phase(self, source_phase: str) -> str:
        return source_phase if self.snapshot is not None else "waiting"

    def fields(self, applied: Snapshot | None, now: float) -> dict:
        rendered = self.snapshot
        return {
            "frames": self.frames, "frame_count": self.frames,
            "native_snapshot_frames": self.native_frames,
            "distinct_native_snapshot_frames": self.distinct_native_frames,
            "repeated_native_snapshot_frames": self.native_frames - self.distinct_native_frames,
            "rendered_sequence": rendered.sequence if rendered else None,
            "rendered_generation": rendered.generation if rendered else None,
            "rendered_sim_time_s": rendered.sim_time_s if rendered else None,
            "rendered_source_is_latest": rendered is not None and applied is not None
                                         and rendered.sequence == applied.sequence,
            "max_body_pose_error": self.pose_error,
            "body_pose_matches": self.pose_error is not None,
            "body_pose_comparison_phase": "last_successful_native_swap",
            "last_render_monotonic_s": self.last_render_at,
            "last_render_age_s": max(0.0, now - self.last_render_at) if self.last_render_at is not None else None,
            "camera_rendered": self.camera,
        }


class RenderTiming:
    """Opt-in CPU wall/CPU process aggregates; no GPU synchronization or queries."""

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.scopes: dict[str, list] = {}
        self.reasons = Counter()
        self.idle_requested = [0, 0.0, 0.0]
        self.started = time.monotonic() if enabled else 0.0
        self.cpu_started = time.process_time() if enabled else 0.0

    def start(self) -> float | None:
        return time.perf_counter() if self.enabled else None

    def stop(self, name: str, started: float | None) -> float | None:
        if started is not None:
            elapsed = time.perf_counter() - started
            item = self.scopes.setdefault(name, [0, 0.0, 0.0])
            item[0] += 1
            item[1] += elapsed
            item[2] = max(item[2], elapsed)
            return elapsed
        return None

    def idle(self, requested_s: float, started: float | None) -> None:
        elapsed = self.stop("idle", started)
        if elapsed is not None:
            self.idle_requested[0] += 1
            self.idle_requested[1] += requested_s
            self.idle_requested[2] = max(self.idle_requested[2], requested_s)

    def draw(self, reasons: tuple[str, ...]) -> None:
        if self.enabled:
            self.reasons.update(reasons)

    def publish(self, writer, state: dict, final: bool = False) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        wall = now - self.started
        if not final and wall < TIMING_PERIOD_S:
            return
        cpu = time.process_time()
        report = {
            "schema": "rmsim_mujoco_viewer_timing_v1",
            "event": "final" if final else "interval", "pid": os.getpid(),
            "reported_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "window_monotonic_s": [self.started, now], "window_wall_s": wall,
            "process_cpu_s": cpu - self.cpu_started,
            "process_cpu_one_core_percent": 100.0 * (cpu - self.cpu_started) / max(wall, 1e-9),
            "scopes_ms": {name: {"count": n, "total": total * 1000.0,
                                  "mean": total * 1000.0 / n, "max": peak * 1000.0}
                          for name, (n, total, peak) in self.scopes.items()},
            "draw_reasons": dict(self.reasons),
            "idle_requested_ms": {
                "count": self.idle_requested[0],
                "total": self.idle_requested[1] * 1000.0,
                "mean": (self.idle_requested[1] * 1000.0 / self.idle_requested[0]
                         if self.idle_requested[0] else None),
                "max": self.idle_requested[2] * 1000.0,
            },
            "idle_actual_scope": "scopes_ms.idle measures the actual event wait; posted events may return before the requested timeout",
            "timing_semantics": "native call wall time; render_submit is not GPU execution time; no glFinish",
            **state,
        }
        writer.update(render_timing=report)
        print("MUJOCO_VIEWER_TIMING " + json.dumps(report, allow_nan=False, separators=(",", ":")), flush=True)
        self.started, self.cpu_started = now, cpu
        self.scopes.clear()
        self.reasons.clear()
        self.idle_requested = [0, 0.0, 0.0]


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


class StatusWriter:
    """Atomic, bounded status publication; transient readers cannot stop rendering."""

    def __init__(self, path: Path, initial: dict):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            raise ValueError("viewer status path already exists; use a fresh session directory")
        self.state = initial
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self._run, name="viewer-status", daemon=True)
        self.thread.start()

    def update(self, urgent: bool = False, **fields: Any) -> None:
        with self.lock:
            self.state.update(fields)
        if urgent:
            self.wake.set()

    def _write(self) -> bool:
        with self.lock:
            report = dict(self.state)
        report["status_written_monotonic_s"] = time.monotonic()
        report["updated_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        temporary = self.path.with_name(self.path.name + "." + str(os.getpid()) + ".tmp")
        text = json.dumps(report, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n"
        for attempt in range(4):
            try:
                temporary.write_text(text, encoding="utf-8", newline="\n")
                temporary.replace(self.path)
                return True
            except PermissionError:
                if attempt < 3:
                    time.sleep(0.005 * (2 ** attempt))
            except OSError as error:
                with self.lock:
                    self.state["last_status_write_error"] = str(error)
                break
        with self.lock:
            self.state["status_write_deferrals"] = self.state.get("status_write_deferrals", 0) + 1
        return False

    def _run(self) -> None:
        while not self.stop.is_set():
            self._write()
            self.wake.wait(STATUS_PERIOD_S)
            self.wake.clear()

    def finish(self, phase: str, **fields: Any) -> None:
        self.stop.set()
        self.wake.set()
        self.thread.join(timeout=2)
        self.update(phase=phase, **fields)
        if not self._write():
            print("MuJoCo viewer could not publish its final status", file=sys.stderr, flush=True)


class ParentWatch:
    """Wait on the exact Windows process handle; never send a process signal."""

    def __init__(self, pid: int | None):
        self.pid = pid
        self.handle = None
        if pid is not None and os.name == "nt":
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32)
            self.kernel.OpenProcess.restype = ctypes.c_void_p
            self.kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
            self.kernel.WaitForSingleObject.restype = ctypes.c_uint32
            self.kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
            self.kernel.CloseHandle.restype = ctypes.c_bool
            self.handle = self.kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
            if not self.handle:
                raise ValueError("cannot observe the specified parent process")

    def alive(self) -> bool:
        if self.pid is None:
            return True
        if self.handle is not None:
            result = self.kernel.WaitForSingleObject(self.handle, 0)
            if result not in (0, 258):
                raise OSError("parent process wait failed")
            return result == 258
        try:
            os.kill(self.pid, 0)  # POSIX existence check; never executed on Windows.
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def close(self) -> None:
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class SnapshotReceiver:
    def __init__(self, port: int, contract: ViewerContract):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024)
            self.socket.bind(("127.0.0.1", port))
            self.socket.settimeout(0.1)
        except BaseException:
            self.socket.close()
            raise
        self.validator = SnapshotValidator(contract)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.latest: tuple[Snapshot, float] | None = None
        self.accepted = 0
        self.sequence_gaps = 0
        self.rejected: Counter[str] = Counter()
        self.error: str | None = None
        self.wake_lock = threading.Lock()
        self.wake_callback: Callable[[], None] | None = None
        self.wake_notifications = 0
        self.thread = threading.Thread(target=self._run, name="viewer-snapshots", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while not self.stop.is_set():
            try:
                packet, address = self.socket.recvfrom(MAX_PACKET_BYTES + 1)
            except socket.timeout:
                continue
            except OSError as error:
                if getattr(error, "winerror", None) == 10040 or error.errno == 10040:
                    with self.lock:
                        self.rejected["packet_size"] += 1
                    continue
                if not self.stop.is_set():
                    with self.lock:
                        self.error = str(error)
                return
            try:
                if address[0] != "127.0.0.1":
                    raise SnapshotError("non_loopback_source")
                snapshot = self.validator.accept(packet)
            except SnapshotError as error:
                with self.lock:
                    self.rejected[error.reason] += 1
                continue
            with self.lock:
                previous = self.latest[0].sequence if self.latest is not None else 0
                self.sequence_gaps += max(0, snapshot.sequence - previous - 1)
                self.latest = (snapshot, time.monotonic())
                self.accepted += 1
            # glfwPostEmptyEvent may run on this receiver thread only after the
            # main thread registers it following successful GLFW initialization.
            # Holding this lock through the call lets close revoke and drain it
            # before any context/window destruction or glfwTerminate.
            with self.wake_lock:
                if self.wake_callback is not None and not self.stop.is_set():
                    try:
                        self.wake_callback()
                    except Exception as error:
                        with self.lock:
                            self.error = "snapshot wake failed: " + str(error)
                        return
                    with self.lock:
                        self.wake_notifications += 1

    def set_wake(self, callback: Callable[[], None] | None) -> None:
        with self.wake_lock:
            if callback is not None and self.stop.is_set():
                raise RuntimeError("cannot attach a wake callback to a closed snapshot receiver")
            self.wake_callback = callback

    def read(self) -> tuple[tuple[Snapshot, float] | None, dict]:
        with self.lock:
            return self.latest, {"accepted_snapshots": self.accepted, "rejected_snapshots": dict(self.rejected),
                                 "unreceived_snapshot_sequences": self.sequence_gaps,
                                 "receiver_error": self.error, "snapshot_wake_notifications": self.wake_notifications}

    def close(self) -> None:
        self.stop.set()
        self.set_wake(None)
        self.socket.close()
        self.thread.join(timeout=1)
        if self.thread.is_alive():
            raise RuntimeError("snapshot receiver did not stop before display teardown")


def recheck_after_draw(receiver: SnapshotReceiver, applied: Snapshot | None,
                       schedule: DrawSchedule) -> tuple[tuple[Snapshot, float] | None, dict]:
    """Keep already-accepted display work pending before calculating an idle wait."""
    latest, status = receiver.read()
    if status["receiver_error"]:
        raise RuntimeError("snapshot receiver failed: " + status["receiver_error"])
    if latest is not None and (applied is None or latest[0].sequence != applied.sequence):
        schedule.request("native_snapshot")
    return latest, status


def camera_state(camera) -> dict:
    """Copy only public camera values; never modify the view or source state."""
    return {"type": int(camera.type), "lookat": [float(value) for value in camera.lookat],
            "distance": float(camera.distance), "azimuth": float(camera.azimuth),
            "elevation": float(camera.elevation)}


def read_manifest(path: Path, token: str) -> tuple[dict, Path, ViewerContract, dict]:
    raw = path.read_bytes()
    manifest = json.loads(raw.decode("utf-8-sig"))
    model_path = (path.parent / manifest["model_xml"]).resolve(strict=True)
    counts = manifest["model_counts"]
    contract = ViewerContract(
        token, manifest["model_sha256"], manifest["physics_identity_sha256"],
        counts["nq"], counts["nv"], counts.get("na", 0), counts["nu"], counts["nbody"],
    )
    identity = {
        "manifest_path": str(path), "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "model_path": str(model_path), "model_sha256": contract.model_sha256,
        "physics_identity_sha256": contract.physics_identity_sha256,
        "geometry_manifest_sha256": manifest["geometry_manifest_sha256"],
        "expected_model_counts": counts,
    }
    if manifest.get("diagnostic_plane_only"):
        raise ValueError("live viewer requires the complete sealed scene model")
    if sha256_file(model_path) != contract.model_sha256:
        raise ValueError("model SHA-256 does not match the sealed manifest")
    return manifest, model_path, contract, identity


def initialize_display_model(mujoco, model, data, manifest: dict) -> None:
    """Only position-derived display fields; never collision, force or integration."""
    counts = manifest["model_counts"]
    for name in ("nq", "nv", "nu", "nbody", "ngeom", "nmesh", "nflex", "nflexvert", "nflexelem"):
        if int(getattr(model, name)) != counts[name]:
            raise ValueError("compiled model dimension mismatch: " + name)
    if model.na != counts.get("na", 0):
        raise ValueError("compiled activation dimension mismatch")
    # Cache only the explicitly sealed world-fixed 2-D flex geometry. Dynamic
    # flexes/skins would need a different display refresh policy and fail here.
    if any(int(value) != 2 for value in model.flex_dim) or any(int(value) != 0 for value in model.flex_interp):
        raise ValueError("viewer static-flex cache requires non-interpolated 2-D flexes")
    if any(int(value) != 0 for value in model.flex_vertbodyid) or model.nskin or model.nplugin:
        raise ValueError("viewer static-flex cache requires world-owned vertices, no skins/plugins")
    initial = manifest.get("initial_qpos")
    if initial is not None:
        if len(initial) != model.nq or any(not math.isfinite(float(value)) for value in initial):
            raise ValueError("invalid sealed initial qpos")
        data.qpos[:] = initial
    mujoco.mj_kinematics(model, data)
    mujoco.mj_comPos(model, data)
    mujoco.mj_camlight(model, data)
    mujoco.mj_flex(model, data)  # Exactly once: every flex vertex belongs to world body 0.


def apply_snapshot(mujoco, model, data, snapshot: Snapshot) -> dict[str, float]:
    data.qpos[:] = snapshot.qpos
    data.qvel[:] = snapshot.qvel
    data.act[:] = snapshot.act
    data.ctrl[:] = snapshot.ctrl  # Display mirror only: there is no solver call or send API.
    data.time = snapshot.sim_time_s
    mujoco.mj_kinematics(model, data)
    mujoco.mj_comPos(model, data)
    mujoco.mj_camlight(model, data)
    error = compare_body_poses(data.xpos, data.xquat, snapshot)
    if error["position_m"] > POSITION_TOLERANCE_M or error["orientation_rad"] > ORIENTATION_TOLERANCE_RAD:
        raise ValueError("native/render body pose mismatch: " + json.dumps(error, sort_keys=True))
    return error


def runtime_identity(mujoco) -> dict:
    result = {"python": sys.executable, "python_package": mujoco.__file__,
              "python_package_version": mujoco.__version__, "native_version": mujoco.mj_versionString()}
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetModuleHandleW.argtypes = (ctypes.c_wchar_p,)
        kernel.GetModuleHandleW.restype = ctypes.c_void_p
        kernel.GetModuleFileNameW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32)
        kernel.GetModuleFileNameW.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        handle = kernel.GetModuleHandleW("mujoco.dll")
        if not handle or not kernel.GetModuleFileNameW(handle, buffer, len(buffer)):
            raise OSError("cannot identify the display runtime DLL")
        result.update(dll_path=buffer.value, dll_sha256=sha256_file(Path(buffer.value)))
    return result


class StaticFlexGpuCache:
    """Explicit display-thread opt-in to the separately built renderer overlay."""

    def __init__(self, requested: bool, runtime: dict):
        self.requested = requested
        self.library = None
        if not requested:
            return
        path = runtime.get("dll_path")
        if not path:
            raise ValueError("static flex GPU cache requires an identified display runtime DLL")
        # Loading this already-identified DLL returns its existing process image;
        # neither PATH nor an unrelated MuJoCo installation selects the extension.
        self.library = ctypes.CDLL(path)
        try:
            self.library.rmsim_mjr_setStaticFlexCacheEnabled.argtypes = (ctypes.c_int,)
            self.library.rmsim_mjr_setStaticFlexCacheEnabled.restype = None
            self.library.rmsim_mjr_staticFlexCacheStatus.argtypes = ()
            self.library.rmsim_mjr_staticFlexCacheStatus.restype = ctypes.c_char_p
        except AttributeError as error:
            raise RuntimeError("the selected display DLL has no RMSim static-flex GPU cache extension") from error
        self.status()  # Validate the extension version before enabling it.
        self.library.rmsim_mjr_setStaticFlexCacheEnabled(1)
        if not self.status()["enabled"]:
            raise RuntimeError("the display runtime did not enable its static-flex GPU cache")

    def status(self) -> dict:
        if self.library is None:
            return {"requested": self.requested, "available": False, "enabled": False}
        result = json.loads(self.library.rmsim_mjr_staticFlexCacheStatus())
        if (result.get("schema") != "rmsim_static_flex_vbo_v1" or
                result.get("version") != "rmsim.viewer-static-flex-vbo.1"):
            raise RuntimeError("unsupported static-flex GPU cache status schema/version")
        return {"requested": self.requested, "available": True, **result}

    def close(self) -> dict:
        # Called after context.free(), while its GLFW context still exists.
        if self.library is not None:
            self.library.rmsim_mjr_setStaticFlexCacheEnabled(0)
        return self.status()


def graphics_identity(glfw, window, context) -> dict:
    """Read the current context once; never create a second context or GPU fence."""
    result = {
        "glfw_library": str(getattr(getattr(glfw, "_glfw", None), "_name", "unknown")),
        "swap_interval_requested": 1,
        "window_samples": getattr(context, "windowSamples", None),
        "window_doublebuffer": bool(getattr(context, "windowDoublebuffer", False)),
        "context_version_major": glfw.get_window_attrib(window, glfw.CONTEXT_VERSION_MAJOR),
        "context_version_minor": glfw.get_window_attrib(window, glfw.CONTEXT_VERSION_MINOR),
        "context_creation_api": glfw.get_window_attrib(window, glfw.CONTEXT_CREATION_API),
    }
    version = glfw.get_version_string()
    result["glfw_version_string"] = version.decode("utf-8", "replace") if isinstance(version, bytes) else str(version)
    try:
        address = glfw.get_proc_address("glGetString")
        address = ctypes.cast(address, ctypes.c_void_p).value if address else None
        if address in (None, 1, 2, 3, ctypes.c_void_p(-1).value):
            raise RuntimeError("glGetString is unavailable in the current context")
        factory = ctypes.WINFUNCTYPE if os.name == "nt" else ctypes.CFUNCTYPE
        get_string = factory(ctypes.c_char_p, ctypes.c_uint)(address)
        for name, enum in (("vendor", 0x1F00), ("renderer", 0x1F01), ("version", 0x1F02)):
            raw = get_string(enum)
            result[name] = raw.decode("utf-8", "replace") if raw else None
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        result["query_error"] = str(error)
    return result


def configure_display_lighting(model) -> dict:
    """Improve collision-geometry readability in this local rendering mirror."""
    headlight = model.vis.headlight
    before = {"active": bool(headlight.active),
              "ambient_rgb": [float(value) for value in headlight.ambient],
              "diffuse_rgb": [float(value) for value in headlight.diffuse],
              "specular_rgb": [float(value) for value in headlight.specular]}
    headlight.active = 1
    headlight.ambient[:] = 0.45
    headlight.diffuse[:] = 0.75
    headlight.specular[:] = 0.10
    return {"scope": "viewer-local visual headlight only; sealed XML and physics unchanged",
            "source": before, "display": {"active": True, "ambient_rgb": [0.45] * 3,
                "diffuse_rgb": [0.75] * 3, "specular_rgb": [0.10] * 3}}


class CameraInput:
    def __init__(self, glfw, mujoco, window, model, camera):
        self.glfw, self.mujoco, self.window = glfw, mujoco, window
        self.model, self.camera = model, camera
        self.follow = True
        self.revision = 0
        self.body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "body")
        if self.body_id < 1:
            raise ValueError("sealed model has no chassis body")
        self.last_cursor = glfw.get_cursor_pos(window)
        camera.type = mujoco.mjtCamera.mjCAMERA_TRACKING
        camera.trackbodyid = self.body_id
        camera.distance = 3.0
        camera.azimuth = 135.0
        camera.elevation = -25.0
        glfw.set_cursor_pos_callback(window, self.move)
        glfw.set_scroll_callback(window, self.scroll)
        glfw.set_key_callback(window, self.key)

    def move(self, window, x, y):
        glfw, mujoco = self.glfw, self.mujoco
        dx, dy = x - self.last_cursor[0], y - self.last_cursor[1]
        self.last_cursor = (x, y)
        left = glfw.get_mouse_button(window, glfw.MOUSE_BUTTON_LEFT) == glfw.PRESS
        right = glfw.get_mouse_button(window, glfw.MOUSE_BUTTON_RIGHT) == glfw.PRESS
        middle = glfw.get_mouse_button(window, glfw.MOUSE_BUTTON_MIDDLE) == glfw.PRESS
        if not (left or right or middle):
            return
        height = max(1, glfw.get_window_size(window)[1])
        shift = (glfw.get_key(window, glfw.KEY_LEFT_SHIFT) == glfw.PRESS or
                 glfw.get_key(window, glfw.KEY_RIGHT_SHIFT) == glfw.PRESS)
        if right:
            self.follow = False
            self.camera.type = mujoco.mjtCamera.mjCAMERA_FREE
            action = mujoco.mjtMouse.mjMOUSE_MOVE_H if shift else mujoco.mjtMouse.mjMOUSE_MOVE_V
        elif left:
            action = mujoco.mjtMouse.mjMOUSE_ROTATE_H if shift else mujoco.mjtMouse.mjMOUSE_ROTATE_V
        else:
            action = mujoco.mjtMouse.mjMOUSE_ZOOM
        # MuJoCo 4.0.1 exposes (model, action, reldx, reldy, camera).
        mujoco.mjv_moveCamera(self.model, action, dx / height, dy / height, self.camera)
        self.revision += 1

    def scroll(self, window, xoffset, yoffset):
        self.mujoco.mjv_moveCamera(self.model, self.mujoco.mjtMouse.mjMOUSE_ZOOM,
                                  0.0, -0.05 * yoffset, self.camera)
        self.revision += 1

    def key(self, window, key, scancode, action, mods):
        glfw, mujoco = self.glfw, self.mujoco
        if action != glfw.PRESS:
            return
        if key == glfw.KEY_ESCAPE:
            glfw.set_window_should_close(window, True)
        elif key == glfw.KEY_F:
            self.follow = not self.follow
            self.camera.type = mujoco.mjtCamera.mjCAMERA_TRACKING if self.follow else mujoco.mjtCamera.mjCAMERA_FREE
            self.camera.trackbodyid = self.body_id
        elif key == glfw.KEY_HOME:
            self.follow = False
            self.camera.type = mujoco.mjtCamera.mjCAMERA_FREE
            self.camera.lookat[:] = self.model.stat.center
            self.camera.distance = max(3.0, 1.2 * self.model.stat.extent)
            self.camera.elevation, self.camera.azimuth = -65.0, 90.0
        if key in (glfw.KEY_F, glfw.KEY_HOME):
            self.revision += 1


def run(args: argparse.Namespace) -> int:
    writer = receiver = parent = context = window = glfw = None
    renderer_cache = None
    optimized_runtime = None
    timing = None
    diagnostic_state = {}
    final_phase, exit_code, reason = "closed", 0, "window_closed"
    try:
        writer = StatusWriter(args.status_file, {
            "schema": STATUS_SCHEMA, "phase": "loading", "pid": os.getpid(),
            "port": args.port, "bound": False,
            "session_token_sha256": hashlib.sha256(args.session_token.encode("ascii")).hexdigest(),
            "generation": None, "sequence": None, "sim_time_s": None, "qpos": None,
            "frames": 0, "frame_count": 0, "snapshot_fields": list(SNAPSHOT_FIELDS),
            "native_snapshot_frames": 0, "rendered_sequence": None,
            "rendered_generation": None, "rendered_sim_time_s": None,
            "max_body_pose_error": None, "body_pose_matches": False,
            "camera_current": None, "camera_rendered": None,
            "last_snapshot_age_s": None, "stale_after_s": STALE_AFTER_S,
            "max_render_hz": args.max_fps, "source_nominal_hz": 30,
            "render_policy": "continuous_diagnostic" if args.continuous_render_diagnostic else "event_driven",
            "timing_diagnostic_enabled": args.timing_diagnostic,
            "renderer_cache": {"requested": args.static_flex_gpu_cache,
                               "available": None, "enabled": False},
            "renderer_runtime_identity": None,
            "idle_redraw_hz": 1.0 / IDLE_REDRAW_PERIOD_S,
            "state_source": "UE authoritative native MuJoCo snapshot",
            "display_only": True, "solver_advances": 0, "commands_sent": 0,
            "source_files": {name: sha256_file(Path(__file__).with_name(name))
                             for name in ("live_viewer.py", "viewer_protocol.py", "viewer_runtime.py")},
        })
        parent = ParentWatch(args.parent_pid)
        if args.static_flex_gpu_cache:
            from viewer_runtime import validate_optimized_viewer_runtime
            optimized_runtime = validate_optimized_viewer_runtime(Path(__file__).resolve().parents[2], Path(__file__))
            writer.update(urgent=True, renderer_runtime_identity=optimized_runtime["runtime_identity"])
        manifest, model_path, contract, identity = read_manifest(args.manifest, args.session_token)
        writer.update(urgent=True, **identity)
        receiver = SnapshotReceiver(args.port, contract)
        writer.update(urgent=True, bound=True)
        # Keep imports local: protocol tests and metadata inspection never open GLFW.
        import mujoco
        import glfw as glfw_module
        import numpy
        glfw = glfw_module
        if mujoco.mj_version() != 4000001:
            raise ValueError("this viewer requires the sealed MuJoCo 4.0.1 API")
        display_runtime = runtime_identity(mujoco)
        if optimized_runtime is not None:
            if (Path(sys.prefix).resolve() != Path(optimized_runtime["environment"]).resolve() or
                    Path(display_runtime["dll_path"]).resolve() != Path(optimized_runtime["dll_path"]).resolve() or
                    display_runtime["dll_sha256"] != optimized_runtime["runtime_identity"]["viewer_dll_sha256"]):
                raise ValueError("loaded display environment/DLL differs from validated optimized runtime")
        writer.update(runtime=display_runtime, dependencies={
            "mujoco": mujoco.__version__, "numpy": numpy.__version__, "glfw": glfw.__version__})
        load_started = time.monotonic()
        model = mujoco.MjModel.from_xml_path(str(model_path))
        data = mujoco.MjData(model)
        initialize_display_model(mujoco, model, data, manifest)
        renderer_cache = StaticFlexGpuCache(args.static_flex_gpu_cache, display_runtime)
        cache_status = renderer_cache.status()
        writer.update(display_lighting=configure_display_lighting(model))
        writer.update(model_load_seconds=time.monotonic() - load_started,
                      compiled_model_counts={name: int(getattr(model, name)) for name in
                          ("nq", "nv", "na", "nu", "nbody", "ngeom", "nmesh", "nflex", "nflexvert", "nflexelem")})
        if not parent.alive():
            reason = "parent_exited_during_load"
            return 0
        if not glfw.init():
            raise RuntimeError("GLFW could not initialize the desktop OpenGL backend")
        glfw.window_hint(glfw.VISIBLE, glfw.TRUE)
        window = glfw.create_window(1280, 800, "MuJoCo - RMSim Live | Waiting for UE", None, None)
        if not window:
            raise RuntimeError("GLFW could not create the MuJoCo window")
        glfw.make_context_current(window)
        glfw.swap_interval(1)
        context = mujoco.MjrContext(model, mujoco.mjtFontScale.mjFONTSCALE_150)
        cache_status = renderer_cache.status()
        writer.update(renderer_cache=cache_status)
        writer.update(graphics=graphics_identity(glfw, window, context))
        scene = mujoco.MjvScene(model, maxgeom=max(1000, model.ngeom + model.nflex + 128))
        options, camera = mujoco.MjvOption(), mujoco.MjvCamera()
        perturb = mujoco.MjvPerturb()  # Always inactive; no input callback can mutate it.
        options.geomgroup[:] = 1
        options.flexgroup[:] = 1
        for flag in (mujoco.mjtVisFlag.mjVIS_FLEXVERT, mujoco.mjtVisFlag.mjVIS_FLEXEDGE,
                     mujoco.mjtVisFlag.mjVIS_FLEXSKIN, mujoco.mjtVisFlag.mjVIS_CONTACTPOINT,
                     mujoco.mjtVisFlag.mjVIS_CONTACTFORCE):
            options.flags[flag] = 0
        options.flags[mujoco.mjtVisFlag.mjVIS_FLEXFACE] = 1
        camera_input = CameraInput(glfw, mujoco, window, model, camera)
        # Initial update builds all original flex faces and normals. The strict
        # world-vertex check above makes these arrays immutable across qpos.
        scene_started = time.monotonic()
        mujoco.mjv_updateScene(model, data, options, perturb, camera, mujoco.mjtCatBit.mjCAT_ALL, scene)
        scene.flags[mujoco.mjtRndFlag.mjRND_SHADOW] = 0
        scene.flags[mujoco.mjtRndFlag.mjRND_REFLECTION] = 0
        writer.update(urgent=True, phase="waiting", window_title="MuJoCo - RMSim Live",
                      static_scene_build_seconds=time.monotonic() - scene_started,
                      static_flex_faces=int(sum(scene.flexfaceused)), scene_maxgeom=int(scene.maxgeom),
                      geometry_policy="full sealed hulls and world-fixed flex faces; no simplification")
        schedule = DrawSchedule(args.max_fps, args.continuous_render_diagnostic)
        pid_file = getattr(args, "occluder_pid_file", None)
        coverage = (FullscreenCoverage(pid_file, glfw.get_win32_window(window))
                    if pid_file is not None and sys.platform == "win32" else None)
        glfw.set_window_refresh_callback(window, lambda _window: schedule.request("refresh"))
        glfw.set_framebuffer_size_callback(window, lambda _window, _width, _height: schedule.request("resize"))
        timing = RenderTiming(args.timing_diagnostic)
        receiver.set_wake(glfw.post_empty_event)
        applied: Snapshot | None = None
        pose_error = None
        presented = PresentedState()
        last_phase = "waiting"
        last_source_phase = "waiting"
        last_camera_revision = camera_input.revision
        last_window_state = None
        while not glfw.window_should_close(window):
            started = timing.start()
            glfw.poll_events()
            timing.stop("event_poll", started)
            if glfw.window_should_close(window):
                break
            if not parent.alive():
                reason = "parent_exited"
                break
            now = time.monotonic()
            latest, receive_status = receiver.read()
            if receive_status["receiver_error"]:
                raise RuntimeError("snapshot receiver failed: " + receive_status["receiver_error"])
            age = None
            if latest is not None:
                snapshot, received_at = latest
                age = max(0.0, now - received_at)
                if applied is None or snapshot.sequence != applied.sequence:
                    started = timing.start()
                    pose_error = apply_snapshot(mujoco, model, data, snapshot)
                    timing.stop("kinematics", started)
                    applied = snapshot
                    schedule.request("native_snapshot")
            source_phase = "waiting" if applied is None else ("stale" if age is None or age > STALE_AFTER_S else "ready")
            if source_phase != last_source_phase:
                schedule.request("source_phase")
            last_source_phase = source_phase
            if camera_input.revision != last_camera_revision:
                schedule.request("camera")
                last_camera_revision = camera_input.revision
            width, height = glfw.get_framebuffer_size(window)
            iconified = bool(glfw.get_window_attrib(window, glfw.ICONIFIED))
            visible = bool(glfw.get_window_attrib(window, glfw.VISIBLE))
            focused = bool(glfw.get_window_attrib(window, glfw.FOCUSED))
            covered = bool(coverage is not None and presented.native_frames and not focused and coverage.covered())
            window_state = (width, height, iconified, visible, focused, covered)
            if window_state != last_window_state:
                schedule.request("window_state")
                last_window_state = window_state
            paused_reason = ("iconified" if iconified else "hidden" if not visible else
                             "empty_framebuffer" if width <= 0 or height <= 0 else
                             "covered_by_owned_fullscreen_ue" if covered else None)
            paused = paused_reason is not None
            draw_reasons = schedule.begin(now, source_phase, paused)
            if draw_reasons:
                timing.draw(draw_reasons)
                # Rebuild only the lightweight geom descriptors and camera/lights;
                # preserve the full static flex face buffers from the first update.
                started = timing.start()
                scene.ngeom = 0
                mujoco.mjv_addGeoms(model, data, options, perturb, mujoco.mjtCatBit.mjCAT_ALL, scene)
                mujoco.mjv_updateCamera(model, data, camera, scene)
                drawn_camera = camera_state(camera)
                mujoco.mjv_makeLights(model, data, scene)
                timing.stop("scene_update", started)
                viewport = mujoco.MjrRect(0, 0, width, height)
                started = timing.start()
                mujoco.mjr_render(viewport, scene, context)
                timing.stop("render_submit", started)
                message = "Waiting for matching UE state" if applied is None else (
                    f"UE time {applied.sim_time_s:.3f}s | generation {applied.generation} | sequence {applied.sequence}")
                if source_phase == "stale":
                    message = f"STALE - frozen at last UE state ({age:.2f}s old)\n" + message
                started = timing.start()
                mujoco.mjr_overlay(mujoco.mjtFont.mjFONT_NORMAL, mujoco.mjtGridPos.mjGRID_TOPLEFT,
                                   viewport, "READ-ONLY | UE native solver authority\n" + message, "", context)
                mujoco.mjr_overlay(mujoco.mjtFont.mjFONT_NORMAL, mujoco.mjtGridPos.mjGRID_BOTTOMLEFT,
                                   viewport, "Drag: rotate / pan | Wheel: zoom | F: follow | Home: whole map | Esc: close", "", context)
                timing.stop("overlay", started)
                started = timing.start()
                glfw.swap_buffers(window)
                timing.stop("swap", started)
                presented.swapped(applied, pose_error, time.monotonic(), drawn_camera)
                cache_status = renderer_cache.status()
                # A packet may have arrived during render/swap. Observe it now,
                # so an elapsed 30 Hz deadline never gains an extra idle timeout.
                latest, receive_status = recheck_after_draw(receiver, applied, schedule)
            status_now = time.monotonic()
            age = max(0.0, status_now - latest[1]) if latest else None
            source_phase = "waiting" if applied is None else ("stale" if age is None or age > STALE_AFTER_S else "ready")
            # Source freshness remains independent of intentional display pause.
            # A first native frame must actually be swapped before ready is possible.
            phase = presented.phase(source_phase)
            if phase != last_phase:
                suffix = {"ready": "Live - Read Only", "stale": "STALE - Frozen", "waiting": "Waiting for UE"}[phase]
                glfw.set_window_title(window, "MuJoCo - RMSim Live | " + suffix)
            writer.update(urgent=(phase != last_phase), phase=phase, source_phase=source_phase,
                          generation=applied.generation if applied else None,
                          sequence=applied.sequence if applied else None,
                          sim_time_s=applied.sim_time_s if applied else None,
                          qpos=list(applied.qpos) if applied else None,
                          accepted_source={"sequence": latest[0].sequence, "generation": latest[0].generation,
                                           "sim_time_s": latest[0].sim_time_s, "qpos": list(latest[0].qpos)} if latest else None,
                          last_snapshot_age_s=age, following_chassis=camera_input.follow,
                           camera_current=camera_state(camera),
                           window_framebuffer=[width, height], window_iconified=iconified,
                           window_visible=visible, window_focused=focused,
                           display_paused=paused, display_paused_reason=paused_reason,
                           rendered_scene_geoms=int(scene.ngeom),
                           renderer_cache=cache_status,
                           **presented.fields(latest[0] if latest else applied, status_now), **receive_status)
            last_phase = phase
            if timing.enabled:
                diagnostic_state = {
                    "render_policy": "continuous_diagnostic" if schedule.continuous else "event_driven",
                    "frames": presented.frames, "native_snapshot_frames": presented.native_frames,
                    "distinct_native_snapshot_frames": presented.distinct_native_frames,
                    "accepted_snapshots": receive_status["accepted_snapshots"],
                    "generation": applied.generation if applied else None,
                    "sequence": applied.sequence if applied else None,
                    "sim_time_s": applied.sim_time_s if applied else None,
                    "rendered_sequence": presented.snapshot.sequence if presented.snapshot else None,
                    "rendered_generation": presented.snapshot.generation if presented.snapshot else None,
                    "rendered_sim_time_s": presented.snapshot.sim_time_s if presented.snapshot else None,
                    "last_snapshot_age_s": age,
                    "source_phase": source_phase, "window_framebuffer": [width, height],
                    "window_iconified": iconified, "window_visible": visible, "window_focused": focused,
                    "display_paused": paused, "camera_revision": camera_input.revision,
                    "display_paused_reason": paused_reason,
                    "unreceived_snapshot_sequences": receive_status["unreceived_snapshot_sequences"],
                    "camera_current": camera_state(camera), "camera_rendered": presented.camera,
                    "snapshot_wake_notifications": receive_status["snapshot_wake_notifications"],
                    "renderer_cache": cache_status,
                }
                timing.publish(writer, diagnostic_state)
            wait = schedule.wait_seconds(time.monotonic(), source_phase, paused)
            if wait > 0:
                started = timing.start()
                glfw.wait_events_timeout(wait)
                timing.idle(wait, started)
        return 0
    except KeyboardInterrupt:
        reason = "interrupted"
        return 0
    except Exception as error:
        final_phase, exit_code, reason = "error", 2, str(error)
        print("MuJoCo live viewer: " + reason, file=sys.stderr, flush=True)
        return exit_code
    finally:
        cleanup_fields = {}
        if timing is not None and writer is not None:
            timing.publish(writer, diagnostic_state, final=True)
        if receiver is not None:
            receiver.close()
        if context is not None:
            context.free()
        if renderer_cache is not None:
            # Publish released resources together with the terminal phase. A
            # transient ready + disabled-cache status would misreport a normal close.
            cleanup_fields["renderer_cache"] = renderer_cache.close()
        if glfw is not None:
            if window is not None:
                glfw.destroy_window(window)
            glfw.terminate()
        if parent is not None:
            parent.close()
        if writer is not None:
            writer.finish(final_phase, exit_code=exit_code, exit_reason=reason,
                          closed_monotonic_s=time.monotonic(), **cleanup_fields)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--session-token", type=normalize_session_token, required=True)
    parser.add_argument("--status-file", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--occluder-pid-file", type=Path,
                        help="Windows supervisor PID handoff for an owned fullscreen UE window")
    parser.add_argument("--max-fps", type=float, default=30.0)
    parser.add_argument("--timing-diagnostic", action="store_true",
                        help="emit five-second render/idle CPU wall-time aggregates; no GPU synchronization")
    parser.add_argument("--continuous-render-diagnostic", action="store_true",
                        help="retain continuous display redraw for an identical-code timing baseline")
    parser.add_argument("--static-flex-gpu-cache", action="store_true",
                        help="opt in to exact immutable flex VBO storage in the separate RMSim display DLL")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not math.isfinite(args.max_fps) or not 1 <= args.max_fps <= 60:
        parser.error("max-fps must be in [1,60]")
    if args.parent_pid is not None and args.parent_pid <= 0:
        parser.error("parent-pid must be positive")
    args.manifest = args.manifest.resolve(strict=True)
    args.status_file = args.status_file.resolve()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
