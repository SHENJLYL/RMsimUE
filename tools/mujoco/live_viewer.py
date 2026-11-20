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
from typing import Any

from viewer_protocol import (
    MAX_PACKET_BYTES, SNAPSHOT_FIELDS, STATUS_SCHEMA,
    Snapshot, SnapshotError, SnapshotValidator, ViewerContract, compare_body_poses,
    normalize_session_token,
)


POSITION_TOLERANCE_M = 1e-8
ORIENTATION_TOLERANCE_RAD = 1e-7
STATUS_PERIOD_S = 0.25
STALE_AFTER_S = 0.75


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
        self.rejected: Counter[str] = Counter()
        self.error: str | None = None
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
                self.latest = (snapshot, time.monotonic())
                self.accepted += 1

    def read(self) -> tuple[tuple[Snapshot, float] | None, dict]:
        with self.lock:
            return self.latest, {"accepted_snapshots": self.accepted, "rejected_snapshots": dict(self.rejected), "receiver_error": self.error}

    def close(self) -> None:
        self.stop.set()
        self.socket.close()
        self.thread.join(timeout=1)


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

    def scroll(self, window, xoffset, yoffset):
        self.mujoco.mjv_moveCamera(self.model, self.mujoco.mjtMouse.mjMOUSE_ZOOM,
                                  0.0, -0.05 * yoffset, self.camera)

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


def run(args: argparse.Namespace) -> int:
    writer = receiver = parent = context = window = glfw = None
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
            "last_snapshot_age_s": None, "stale_after_s": STALE_AFTER_S,
            "max_render_hz": args.max_fps, "source_nominal_hz": 30,
            "state_source": "UE authoritative native MuJoCo snapshot",
            "display_only": True, "solver_advances": 0, "commands_sent": 0,
            "source_files": {name: sha256_file(Path(__file__).with_name(name))
                             for name in ("live_viewer.py", "viewer_protocol.py")},
        })
        parent = ParentWatch(args.parent_pid)
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
        writer.update(runtime=runtime_identity(mujoco), dependencies={
            "mujoco": mujoco.__version__, "numpy": numpy.__version__, "glfw": glfw.__version__})
        load_started = time.monotonic()
        model = mujoco.MjModel.from_xml_path(str(model_path))
        data = mujoco.MjData(model)
        initialize_display_model(mujoco, model, data, manifest)
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
        applied: Snapshot | None = None
        pose_error = None
        frames = 0
        native_frames = 0
        rendered: Snapshot | None = None
        last_native_render = None
        next_frame = time.monotonic()
        last_phase = "waiting"
        while not glfw.window_should_close(window):
            glfw.poll_events()
            if not parent.alive():
                reason = "parent_exited"
                break
            now = time.monotonic()
            if now < next_frame:
                glfw.wait_events_timeout(min(next_frame - now, 0.02))
                continue
            latest, receive_status = receiver.read()
            if receive_status["receiver_error"]:
                raise RuntimeError("snapshot receiver failed: " + receive_status["receiver_error"])
            age = None
            if latest is not None:
                snapshot, received_at = latest
                age = max(0.0, now - received_at)
                if applied is None or snapshot.sequence != applied.sequence:
                    pose_error = apply_snapshot(mujoco, model, data, snapshot)
                    applied = snapshot
            phase = "waiting" if applied is None else ("stale" if age is None or age > STALE_AFTER_S else "ready")
            width, height = glfw.get_framebuffer_size(window)
            if width > 0 and height > 0:
                # Rebuild only the lightweight geom descriptors and camera/lights;
                # preserve the full static flex face buffers from the first update.
                scene.ngeom = 0
                mujoco.mjv_addGeoms(model, data, options, perturb, mujoco.mjtCatBit.mjCAT_ALL, scene)
                mujoco.mjv_updateCamera(model, data, camera, scene)
                mujoco.mjv_makeLights(model, data, scene)
                viewport = mujoco.MjrRect(0, 0, width, height)
                mujoco.mjr_render(viewport, scene, context)
                message = "Waiting for matching UE state" if applied is None else (
                    f"UE time {applied.sim_time_s:.3f}s | generation {applied.generation} | sequence {applied.sequence}")
                if phase == "stale":
                    message = f"STALE - frozen at last UE state ({age:.2f}s old)\n" + message
                mujoco.mjr_overlay(mujoco.mjtFont.mjFONT_NORMAL, mujoco.mjtGridPos.mjGRID_TOPLEFT,
                                   viewport, "READ-ONLY | UE native solver authority\n" + message, "", context)
                mujoco.mjr_overlay(mujoco.mjtFont.mjFONT_NORMAL, mujoco.mjtGridPos.mjGRID_BOTTOMLEFT,
                                   viewport, "Drag: rotate / pan | Wheel: zoom | F: follow | Home: whole map | Esc: close", "", context)
                glfw.swap_buffers(window)
                frames += 1
                if applied is not None:
                    rendered = applied
                    native_frames += 1
                    last_native_render = time.monotonic()
                writer.update(last_render_monotonic_s=time.monotonic())
            if rendered is None:
                phase = "waiting"
            elif last_native_render is not None and now - last_native_render > STALE_AFTER_S:
                phase = "stale"
            if phase != last_phase:
                suffix = {"ready": "Live - Read Only", "stale": "STALE - Frozen", "waiting": "Waiting for UE"}[phase]
                glfw.set_window_title(window, "MuJoCo - RMSim Live | " + suffix)
            writer.update(urgent=(phase != last_phase), phase=phase, frames=frames, frame_count=frames,
                          generation=applied.generation if applied else None,
                          sequence=applied.sequence if applied else None,
                          sim_time_s=applied.sim_time_s if applied else None,
                          qpos=list(applied.qpos) if applied else None,
                          accepted_source={"sequence": latest[0].sequence, "generation": latest[0].generation,
                                           "sim_time_s": latest[0].sim_time_s, "qpos": list(latest[0].qpos)} if latest else None,
                          native_snapshot_frames=native_frames,
                          rendered_sequence=rendered.sequence if rendered else None,
                          rendered_generation=rendered.generation if rendered else None,
                          rendered_sim_time_s=rendered.sim_time_s if rendered else None,
                          max_body_pose_error=pose_error, body_pose_matches=pose_error is not None,
                          last_snapshot_age_s=age, following_chassis=camera_input.follow,
                          window_framebuffer=[width, height], rendered_scene_geoms=int(scene.ngeom), **receive_status)
            last_phase = phase
            # Drop missed display deadlines. Solver time comes only from snapshots.
            next_frame = now + 1.0 / args.max_fps
        return 0
    except KeyboardInterrupt:
        reason = "interrupted"
        return 0
    except Exception as error:
        final_phase, exit_code, reason = "error", 2, str(error)
        print("MuJoCo live viewer: " + reason, file=sys.stderr, flush=True)
        return exit_code
    finally:
        if receiver is not None:
            receiver.close()
        if context is not None:
            context.free()
        if glfw is not None:
            if window is not None:
                glfw.destroy_window(window)
            glfw.terminate()
        if parent is not None:
            parent.close()
        if writer is not None:
            writer.finish(final_phase, exit_code=exit_code, exit_reason=reason, closed_monotonic_s=time.monotonic())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--session-token", type=normalize_session_token, required=True)
    parser.add_argument("--status-file", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--max-fps", type=float, default=30.0)
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
