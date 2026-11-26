"""World-frame angular tracker, PnP 3D range solver, and 17mm ballistic aimer (immune to gimbal self-rotation)."""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

# Official RMUC armor lightbar 3D corners in armor frame (meters): [tl, tr, br, bl]
SMALL_ARMOR_3D = np.array(
    [
        [-0.0675, -0.028, 0.0],
        [+0.0675, -0.028, 0.0],
        [+0.0675, +0.028, 0.0],
        [-0.0675, +0.028, 0.0],
    ],
    dtype=np.float64,
)

BIG_ARMOR_3D = np.array(
    [
        [-0.115, -0.028, 0.0],
        [+0.115, -0.028, 0.0],
        [+0.115, +0.028, 0.0],
        [-0.115, +0.028, 0.0],
    ],
    dtype=np.float64,
)


@dataclass
class AimSolution:
    locked: bool
    shoot: bool
    target_yaw: float
    target_pitch: float
    err_yaw: float
    err_pitch: float
    distance_m: float
    tof_s: float
    pos_cam: tuple[float, float, float]


def solve_armor_pnp(keypoints: np.ndarray, armor_type: str, K: np.ndarray) -> np.ndarray | None:
    """Return 3D position [x_right, y_down, z_forward] in camera optical frame (meters)."""
    obj_pts = BIG_ARMOR_3D if armor_type == "big" else SMALL_ARMOR_3D
    img_pts = keypoints.astype(np.float64).reshape(4, 1, 2)
    dist = np.zeros((5, 1), dtype=np.float64)
    ok, _rvec, tvec = cv2.solvePnP(obj_pts, img_pts, K, dist, flags=cv2.SOLVEPNP_IPPE)
    if not ok:
        return None
    p = tvec.reshape(3)
    if not np.all(np.isfinite(p)) or p[2] < 0.25 or p[2] > 18.0:
        return None
    return p


def ray_to_world_angles(world_yaw: float, world_pitch: float, u: float, v: float) -> tuple[float, float]:
    """Transform camera normalized ray (u=x/z right, v=y/z down) to rmsim_world (yaw CCW+, pitch down+)."""
    cy, sy = math.cos(world_yaw), math.sin(world_yaw)
    cp, sp = math.cos(world_pitch), math.sin(world_pitch)
    forward = cp - v * sp
    wx = forward * cy + u * sy
    wy = forward * sy - u * cy
    wz = -sp - v * cp
    return math.atan2(wy, wx), math.atan2(-wz, math.hypot(wx, wy))


def ballistic_drop_angle(dist_m: float, raw_pitch_rad: float, muzzle_speed_mps: float = 24.5, g: float = 9.81) -> tuple[float, float]:
    """Return (drop_rad, tof_s) for 17mm projectile. Subtract drop_rad from down-positive raw_pitch_rad."""
    d = max(0.4, min(18.0, float(dist_m)))
    v0 = max(10.0, float(muzzle_speed_mps))
    horiz = max(0.3, d * math.cos(raw_pitch_rad))
    drop_rad = 0.0
    tof = horiz / v0
    for _ in range(3):
        eff_pitch = raw_pitch_rad - drop_rad
        vx = max(5.0, v0 * math.cos(eff_pitch))
        tof = horiz / vx
        drop_m = 0.5 * g * tof * tof
        drop_rad = math.atan2(drop_m, horiz)
    return min(0.32, max(0.0, drop_rad)), tof


class WorldAngularTracker:
    """Inertial world-frame tracker (yaw, pitch, dist + damped angular rate).

    - Vehicles: zero-lag world-angle tracking with bounded angular velocity lead.
    - Rotating Outpost: learns stable constant rotation rate from same-plate steps while
      ignoring plate-switch jumps, and holds the rotor center angle during brief plate gaps.
    - Base: static center tracking (zero velocity lead).
    """

    def __init__(self, max_lost_s: float = 0.90) -> None:
        self.max_lost_s = float(max_lost_s)
        self.yaw = 0.0
        self.pitch = 0.0
        self.dist = 3.0
        self.yaw_rate = 0.0
        self.pitch_rate = 0.0
        self.outpost_center_yaw: float | None = None
        self.outpost_yaw_rate = 0.0
        self.initialized = False
        self.last_t = 0.0
        self.track_hits = 0

    def reset(self) -> None:
        self.initialized = False
        self.yaw_rate = 0.0
        self.pitch_rate = 0.0
        self.outpost_center_yaw = None
        self.outpost_yaw_rate = 0.0
        self.track_hits = 0

    def update(
        self,
        meas: tuple[float, float, float] | None,
        t_sec: float,
        *,
        target_kind: str = "vehicle",  # "vehicle" | "outpost" | "base"
    ) -> tuple[float, float, float, float, float, int] | None:
        if meas is None:
            if not self.initialized or (t_sec - self.last_t) > self.max_lost_s:
                self.reset()
                return None
            dt = max(0.0, min(0.25, t_sec - self.last_t))
            if target_kind == "outpost":
                # Wait at rotor center for the next rotating plate to enter the firing window
                hold_yaw = self.outpost_center_yaw if self.outpost_center_yaw is not None else self.yaw
                return hold_yaw, self.pitch, self.dist, self.outpost_yaw_rate, 0.0, self.track_hits
            if target_kind == "base":
                return self.yaw, self.pitch, self.dist, 0.0, 0.0, self.track_hits
            pred_yaw = math.atan2(
                math.sin(self.yaw + self.yaw_rate * dt * 0.5),
                math.cos(self.yaw + self.yaw_rate * dt * 0.5),
            )
            pred_pitch = max(-0.45, min(0.45, self.pitch + self.pitch_rate * dt * 0.5))
            return pred_yaw, pred_pitch, self.dist, self.yaw_rate * 0.5, self.pitch_rate * 0.5, self.track_hits

        myaw, mpitch, mdist = meas
        if not self.initialized or (t_sec - self.last_t) > self.max_lost_s:
            self.yaw = myaw
            self.pitch = mpitch
            self.dist = mdist
            self.yaw_rate = 0.0
            self.pitch_rate = 0.0
            self.outpost_center_yaw = myaw if target_kind == "outpost" else None
            self.outpost_yaw_rate = 0.0
            self.initialized = True
            self.last_t = t_sec
            self.track_hits = 1
            return self.yaw, self.pitch, self.dist, 0.0, 0.0, self.track_hits

        dt = max(0.015, min(0.30, t_sec - self.last_t))
        dyaw = math.remainder(myaw - self.yaw, 2.0 * math.pi)
        dpitch = mpitch - self.pitch
        ang_jump = math.hypot(dyaw, dpitch)

        if target_kind == "outpost":
            # Track rotor center with slow EMA
            if self.outpost_center_yaw is None:
                self.outpost_center_yaw = myaw
            else:
                cdiff = math.remainder(myaw - self.outpost_center_yaw, 2.0 * math.pi)
                if abs(cdiff) < 0.35:
                    self.outpost_center_yaw = math.atan2(
                        math.sin(self.outpost_center_yaw + 0.12 * cdiff),
                        math.cos(self.outpost_center_yaw + 0.12 * cdiff),
                    )
                else:
                    self.outpost_center_yaw = myaw

            # Same-plate motion (small continuous delta): learn true physical rotation rate!
            # Expected tangential speed v = 0.8*pi*0.276 = 0.694 m/s -> |yaw_rate| ≈ 0.694 / dist
            max_phys_rate = min(0.65, 1.15 / max(1.0, mdist))
            if abs(dyaw) < 0.075 and dt <= 0.16:
                raw_yr = dyaw / dt
                if abs(raw_yr) <= max_phys_rate * 1.4:
                    clamped_yr = max(-max_phys_rate, min(max_phys_rate, raw_yr))
                    self.outpost_yaw_rate = 0.60 * self.outpost_yaw_rate + 0.40 * clamped_yr
            # Plate switch (0.075..0.35 rad): keep learned rotation rate & track_hits intact!
            elif ang_jump > 0.45:
                self.outpost_yaw_rate = 0.0
                self.track_hits = 0

            # Outpost's 3 rotating plates sit at DIFFERENT heights (135.6 / 146.4 / 155.3 cm)!
            # Use exact zero-lag pitch of the current plate instead of blending with the previous plate.
            self.yaw = myaw
            self.pitch = mpitch
            self.dist = 0.4 * self.dist + 0.6 * mdist
            self.yaw_rate = self.outpost_yaw_rate
            self.pitch_rate = 0.0
            self.last_t = t_sec
            self.track_hits += 1
            return self.yaw, self.pitch, self.dist, self.outpost_yaw_rate, 0.0, self.track_hits

        # Target jump gate (0.45 rad ≈ 26 deg) for vehicles / base
        if ang_jump > 0.45:
            self.yaw = myaw
            self.pitch = mpitch
            self.dist = mdist
            self.yaw_rate = 0.0
            self.pitch_rate = 0.0
            self.last_t = t_sec
            self.track_hits = 1
            return self.yaw, self.pitch, self.dist, 0.0, 0.0, self.track_hits

        if target_kind == "base":
            self.yaw_rate = 0.0
            self.pitch_rate = 0.0
            alpha = 0.85
        else:
            raw_yr = max(-1.6, min(1.6, dyaw / dt))
            raw_pr = max(-0.8, min(0.8, dpitch / dt))
            self.yaw_rate = 0.55 * self.yaw_rate + 0.45 * raw_yr
            self.pitch_rate = 0.55 * self.pitch_rate + 0.45 * raw_pr
            alpha = 1.0  # Zero position lag for moving vehicles

        self.yaw = math.atan2(
            math.sin(self.yaw + alpha * dyaw),
            math.cos(self.yaw + alpha * dyaw),
        )
        self.pitch = self.pitch + alpha * dpitch
        self.dist = 0.4 * self.dist + 0.6 * mdist
        self.last_t = t_sec
        self.track_hits += 1
        return self.yaw, self.pitch, self.dist, self.yaw_rate, self.pitch_rate, self.track_hits


class Kalman3DTracker:
    """Compatibility wrapper kept for offline test script."""

    def __init__(self, max_lost_s: float = 0.45) -> None:
        self.max_lost_s = max_lost_s
        self.x = np.zeros(6, dtype=np.float64)
        self.initialized = False
        self.last_t = 0.0
        self.track_hits = 0

    def reset(self) -> None:
        self.initialized = False
        self.track_hits = 0

    def update(self, meas_xyz: np.ndarray | None, t_sec: float) -> tuple[np.ndarray, np.ndarray, int] | None:
        if meas_xyz is None:
            if not self.initialized or (t_sec - self.last_t) > self.max_lost_s:
                self.reset()
                return None
            return self.x[:3].copy(), self.x[3:6].copy(), self.track_hits
        if not self.initialized or (t_sec - self.last_t) > self.max_lost_s:
            self.x[:3] = meas_xyz
            self.x[3:6] = 0.0
            self.initialized = True
            self.last_t = t_sec
            self.track_hits = 1
            return self.x[:3].copy(), self.x[3:6].copy(), self.track_hits
        dt = max(0.02, min(0.25, t_sec - self.last_t))
        self.x[3:6] = 0.5 * self.x[3:6] + 0.5 * ((meas_xyz - self.x[:3]) / dt)
        self.x[:3] = meas_xyz
        self.last_t = t_sec
        self.track_hits += 1
        return self.x[:3].copy(), self.x[3:6].copy(), self.track_hits


class BallisticAimer:
    """17mm RMUC 2027 ballistic lead + gravity drop solver (24.5 m/s)."""

    def __init__(
        self,
        muzzle_speed_mps: float = 24.5,
        gravity: float = 9.81,
        latency_s: float = 0.08,
        shoot_tol_rad: float = math.radians(5.5),
        structure_shoot_tol_rad: float = math.radians(8.5),
    ) -> None:
        self.v0 = float(muzzle_speed_mps)
        self.g = float(gravity)
        self.latency_s = float(latency_s)
        self.shoot_tol_rad = float(shoot_tol_rad)
        self.structure_shoot_tol_rad = float(structure_shoot_tol_rad)

    def solve_world(
        self,
        world_yaw: float,
        world_pitch: float,
        dist_m: float,
        yaw_rate: float,
        pitch_rate: float,
        cur_yaw: float,
        cur_pitch: float,
        track_hits: int,
        *,
        is_structure: bool = False,
        target_kind: str | None = None,
    ) -> AimSolution:
        kind = target_kind or ("base" if is_structure else "vehicle")
        drop_rad, tof = ballistic_drop_angle(dist_m, world_pitch, self.v0, self.g)
        # Apply angular lead to BOTH moving vehicles AND rotating outpost (0.8*pi rad/s -> ~14cm lead at 3m)!
        lead_t = 0.0 if kind == "base" else (tof + self.latency_s)
        max_lead = 0.14 if kind == "outpost" else 0.22
        lead_yaw = max(-max_lead, min(max_lead, yaw_rate * lead_t))
        lead_pitch = 0.0 if kind != "vehicle" else max(-0.10, min(0.10, pitch_rate * lead_t))

        target_yaw = math.atan2(math.sin(world_yaw + lead_yaw), math.cos(world_yaw + lead_yaw))
        target_pitch = max(-0.45, min(0.45, world_pitch + lead_pitch - drop_rad))
        err_yaw = math.remainder(target_yaw - cur_yaw, 2.0 * math.pi)
        err_pitch = target_pitch - cur_pitch
        is_struct = kind in ("outpost", "base")
        min_hits = 1 if is_struct else 2
        tol = self.structure_shoot_tol_rad if is_struct else self.shoot_tol_rad
        can_shoot = track_hits >= min_hits and abs(err_yaw) <= tol and abs(err_pitch) <= tol
        return AimSolution(
            locked=True,
            shoot=can_shoot,
            target_yaw=target_yaw,
            target_pitch=target_pitch,
            err_yaw=err_yaw,
            err_pitch=err_pitch,
            distance_m=dist_m,
            tof_s=tof,
            pos_cam=(0.0, 0.0, dist_m),
        )

    def solve(
        self,
        pos_cam: np.ndarray,
        vel_cam: np.ndarray,
        cur_yaw: float,
        cur_pitch: float,
        track_hits: int,
        *,
        is_structure: bool = False,
    ) -> AimSolution:
        """Offline compatibility entry from camera-frame PnP vector."""
        aim_z = max(0.1, float(pos_cam[2]))
        u = float(pos_cam[0]) / aim_z
        v = float(pos_cam[1]) / aim_z
        wyaw, wpitch = ray_to_world_angles(cur_yaw, cur_pitch, u, v)
        dist = float(np.linalg.norm(pos_cam))
        return self.solve_world(wyaw, wpitch, dist, 0.0, 0.0, cur_yaw, cur_pitch, track_hits, is_structure=is_structure)
