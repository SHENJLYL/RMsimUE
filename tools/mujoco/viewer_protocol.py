"""Validated, receive-only snapshots for the RMSim MuJoCo display.

Coordinates and quaternion order are native MuJoCo SI / wxyz.  This module
contains no socket, renderer or physics API and can be tested independently.
"""
from __future__ import annotations

from dataclasses import dataclass
import hmac
import json
import math
import re
from typing import Any, Sequence


STATE_SCHEMA = "rmsim_mujoco_viewer_state_v1"
STATUS_SCHEMA = "rmsim_mujoco_viewer_status_v1"
MAX_PACKET_BYTES = 60 * 1024
MAX_EXACT_INTEGER = (1 << 53) - 1
SNAPSHOT_FIELDS = (
    "generation", "sequence", "sim_time_s", "qpos", "qvel", "act", "ctrl",
    "body_xpos", "body_xquat",
)


class SnapshotError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def normalize_session_token(value: str) -> str:
    """Hex token spelling is case-insensitive; native publishes lowercase."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", value):
        raise ValueError("session token must be 32 hexadecimal characters")
    return value.lower()


def _integer(value: Any, name: str, maximum: int = MAX_EXACT_INTEGER) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        raise SnapshotError("invalid_" + name)
    return value


def _number(value: Any, name: str) -> float:
    if type(value) not in (int, float):
        raise SnapshotError("invalid_" + name)
    try:
        value = float(value)
    except (ValueError, OverflowError) as error:
        raise SnapshotError("invalid_" + name) from error
    if not math.isfinite(value):
        raise SnapshotError("nonfinite_" + name)
    return value


def _vector(value: Any, count: int, name: str) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != count:
        raise SnapshotError("shape_" + name)
    return tuple(_number(item, name) for item in value)


def _matrix(value: Any, rows: int, columns: int, name: str) -> tuple[tuple[float, ...], ...]:
    if not isinstance(value, list) or len(value) != rows:
        raise SnapshotError("shape_" + name)
    return tuple(_vector(row, columns, name) for row in value)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for name, value in pairs:
        if name in result:
            raise SnapshotError("duplicate_json_key")
        result[name] = value
    return result


@dataclass(frozen=True)
class ViewerContract:
    session_token: str
    model_sha256: str
    physics_identity_sha256: str
    nq: int
    nv: int
    na: int
    nu: int
    nbody: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_token", normalize_session_token(self.session_token))
        for name in ("model_sha256", "physics_identity_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", getattr(self, name)):
                raise ValueError(name + " must be a lowercase SHA-256")
        for name in ("nq", "nv", "na", "nu", "nbody"):
            _integer(getattr(self, name), name, 100000)
        if not self.nq or not self.nv or not self.nbody:
            raise ValueError("model dimensions must be nonzero")


@dataclass(frozen=True)
class Snapshot:
    generation: int
    sequence: int
    sim_time_s: float
    qpos: tuple[float, ...]
    qvel: tuple[float, ...]
    act: tuple[float, ...]
    ctrl: tuple[float, ...]
    body_xpos: tuple[tuple[float, ...], ...]
    body_xquat: tuple[tuple[float, ...], ...]


class SnapshotValidator:
    """Accept one sender lifetime; reset changes generation, never sequence."""

    def __init__(self, contract: ViewerContract):
        self.contract = contract
        self.latest: Snapshot | None = None

    def accept(self, packet: bytes) -> Snapshot:
        if not packet or len(packet) > MAX_PACKET_BYTES:
            raise SnapshotError("packet_size")
        try:
            value = json.loads(packet.decode("utf-8"), object_pairs_hook=_unique_object)
        except SnapshotError:
            raise
        except (UnicodeDecodeError, ValueError, RecursionError) as error:
            raise SnapshotError("invalid_json") from error
        if not isinstance(value, dict) or value.get("schema") != STATE_SCHEMA:
            raise SnapshotError("schema")
        token = value.get("session_token")
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", token) or not hmac.compare_digest(token.lower(), self.contract.session_token):
            raise SnapshotError("session_token")
        for name in ("model_sha256", "physics_identity_sha256"):
            if value.get(name) != getattr(self.contract, name):
                raise SnapshotError(name)
        for name in ("nq", "nv", "na", "nu", "nbody"):
            if _integer(value.get(name), name, 100000) != getattr(self.contract, name):
                raise SnapshotError("dimension_" + name)
        generation = _integer(value.get("generation"), "generation")
        sequence = _integer(value.get("sequence"), "sequence")
        sim_time_s = _number(value.get("sim_time_s"), "sim_time_s")
        if sim_time_s < 0:
            raise SnapshotError("negative_sim_time")
        if self.latest is not None:
            if generation < self.latest.generation:
                raise SnapshotError("old_generation")
            if sequence <= self.latest.sequence:
                raise SnapshotError("out_of_order_sequence")
            if generation == self.latest.generation and sim_time_s < self.latest.sim_time_s - 1e-12:
                raise SnapshotError("time_regression")
        qpos = _vector(value.get("qpos"), self.contract.nq, "qpos")
        qvel = _vector(value.get("qvel"), self.contract.nv, "qvel")
        act = _vector(value.get("act"), self.contract.na, "act")
        ctrl = _vector(value.get("ctrl"), self.contract.nu, "ctrl")
        body_xpos = _matrix(value.get("body_xpos"), self.contract.nbody, 3, "body_xpos")
        body_xquat = _matrix(value.get("body_xquat"), self.contract.nbody, 4, "body_xquat")
        for quaternion in body_xquat:
            if abs(sum(component * component for component in quaternion) - 1.0) > 1e-6:
                raise SnapshotError("nonunit_body_quaternion")
        snapshot = Snapshot(generation, sequence, sim_time_s, qpos, qvel, act, ctrl, body_xpos, body_xquat)
        self.latest = snapshot
        return snapshot


def compare_body_poses(
    positions: Sequence[Sequence[float]],
    quaternions: Sequence[Sequence[float]],
    snapshot: Snapshot,
) -> dict[str, float]:
    """Compare every body; q and -q denote the same orientation."""
    if len(positions) != len(snapshot.body_xpos) or len(quaternions) != len(snapshot.body_xquat):
        raise ValueError("render/native body counts differ")
    position_error = quaternion_error = angle_error = 0.0
    for position, quaternion, expected_position, expected_quaternion in zip(
        positions, quaternions, snapshot.body_xpos, snapshot.body_xquat
    ):
        if any(not math.isfinite(float(item)) for item in (*position, *quaternion)):
            raise ValueError("nonfinite rendered body pose")
        position_error = max(position_error, math.dist(position, expected_position))
        sign = 1.0 if sum(a * b for a, b in zip(quaternion, expected_quaternion)) >= 0 else -1.0
        difference = [float(a) - sign * b for a, b in zip(quaternion, expected_quaternion)]
        total = [float(a) + sign * b for a, b in zip(quaternion, expected_quaternion)]
        quaternion_error = max(quaternion_error, max(abs(item) for item in difference))
        # Chord ratio is stable near zero, where acos(dot(q1,q2)) loses precision.
        angle_error = max(angle_error, 4.0 * math.atan2(math.hypot(*difference), math.hypot(*total)))
    return {"position_m": position_error, "orientation_rad": angle_error, "quaternion_linf": quaternion_error}
