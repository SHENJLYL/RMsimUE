"""ROS 2 sp_vision_25 Auto-Aim node for BOTH RMUC 2027 UE5 Simulator and Real Robot deployment.

Pipeline:
  Dual-engine (OpenVINO YOLOv8 + full-res HSV lightbar & outpost sticker + tiny_resnet)
  -> PnP (with real lens distortion D) / lightbar 3D range
  -> Inertial world angular tracker (immune to chassis/gimbal spin, rotating outpost phase lead,
     zero-lag pitch across 3-height outpost plates)
  -> 17mm ballistic gravity drop + latency/TOF lead + camera-to-barrel calibration
  -> sp_msgs/AutoAimCommandMsg.

Sim & Real Robot Dual-Mode Interfaces:
  - Camera: configurable `camera_topic` & `camera_info_topic` (defaults to /{robot_id}/camera/*)
  - Attitude sync: automatically uses timestamp-interpolated feedback from:
      1) Simulator `/rmsim/{robot_id}/state` (when present)
      2) Real robot `sensor_msgs/Imu` (`imu_topic`, if configured)
      3) Real robot `sensor_msgs/JointState` (`joint_state_topic`)
  - Referee: `referee_mode=auto` ingests BOTH simulator `/rmsim/referee/state` AND native
      `dji_referee_protocol` topics (`/referee/common/game_status`, `robot_performance`,
      `robot_hp`, `shoot_data`, `/referee/parsed/common/self_color`), with `enemy_color`
      manual override (`red`/`blue`) for bench testing without referee serial.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

_DLL_HANDLES = []
if os.name == "nt":
    _ws = Path(__file__).resolve().parents[3]
    for _overlay, _pkgs in (
        ("install_control", ("sp_msgs", "pb_rm_interfaces")),
        ("install_behavior", ("dji_referee_protocol",)),
    ):
        for _pkg in _pkgs:
            _bin = _ws / _overlay / _pkg / "bin"
            if _bin.is_dir():
                _bin_str = str(_bin)
                if hasattr(os, "add_dll_directory"):
                    _DLL_HANDLES.append(os.add_dll_directory(_bin_str))
                if _bin_str not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = _bin_str + os.pathsep + os.environ.get("PATH", "")
            _site = _ws / _overlay / _pkg / "Lib" / "site-packages"
            if _site.is_dir() and str(_site) not in sys.path:
                sys.path.insert(0, str(_site))

    try:
        from rmsim_bridge.native_transport import load_windows_fastrtps
        load_windows_fastrtps()
    except Exception:
        pass

    try:
        from rmsim_bridge.control_dlls import load_package_dlls
        load_package_dlls("sp_msgs", "pb_rm_interfaces", "dji_referee_protocol")
    except Exception:
        pass

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from geometry_msgs.msg import PoseWithCovarianceStamped
from sensor_msgs.msg import CameraInfo, Image, Imu, JointState
from std_msgs.msg import Int32, String

try:
    from ament_index_python.packages import get_package_share_directory
except Exception:
    get_package_share_directory = None

try:
    from sp_msgs.msg import AutoAimCommandMsg
    HAS_SP_MSGS = True
except ImportError:
    HAS_SP_MSGS = False

try:
    from dji_referee_protocol.msg import (
        GameStatus as DjiGameStatus,
        RobotHP as DjiRobotHP,
        RobotPerformance as DjiRobotPerformance,
        SelfColor as DjiSelfColor,
        ShootData as DjiShootData,
    )
    HAS_DJI_REFEREE = True
except ImportError:
    HAS_DJI_REFEREE = False

try:
    from rmsim_bridge.protocol import MATCH_FIRE_OBSERVATION_NS, MATCH_TRACK_MEMORY_NS
except Exception:
    MATCH_FIRE_OBSERVATION_NS = 150_000_000
    MATCH_TRACK_MEMORY_NS = 350_000_000

from .detector import ArmorDetection, SpVisionYoloV8Detector
from .solver_tracker import AimSolution, BallisticAimer, WorldAngularTracker, ray_to_world_angles, solve_armor_pnp

BEHAVIOR_COMMAND_NS = 300_000_000
TRACK_HOLD_NS = 1_100_000_000  # Keep gimbal locked on predicted world angle for 1.1s during brief occlusion/face switch
SIM_HANDOFF_HOLD_NS = 220_000_000
SIM_HANDOFF_GAP_NS = 250_000_000


@dataclass
class PlateObservation:
    name: str
    yaw: float
    pitch: float
    stamp_ns: int
    count: int = 1

    def separation(self, other: PlateObservation) -> float:
        return math.hypot(math.remainder(self.yaw - other.yaw, 2.0 * math.pi), self.pitch - other.pitch)

ARMOR_LABEL_IDS = {
    "one": 0,
    "two": 1,
    "three": 2,
    "four": 3,
    "five": 4,
    "sentry": 5,
    "outpost": 6,
    "base": 7,
}

TARGET_PRIORITY = {
    "sentry": 5,
    "one": 4,
    "three": 3,
    "four": 3,
    "five": 3,
    "two": 2,
    "outpost": 2,
    "base": 1,
}

SECONDARY_TARGETS = {
    "ENEMY_VEHICLE_FIRST": None,
    "ENEMY_VEHICLE_THEN_OUTPOST": "outpost",
    "ENEMY_VEHICLE_THEN_BASE": "base",
}

VEHICLE_NAMES = {"one", "two", "three", "four", "five", "sentry"}
VEHICLE_HP_KEYS = {
    "one": "1_robot_hp",
    "two": "2_robot_hp",
    "three": "3_robot_hp",
    "four": "4_robot_hp",
    "five": "5_robot_hp",
    "sentry": "7_robot_hp",
}


def _default_assets_dir() -> Path:
    local_assets = Path(__file__).resolve().parents[1] / "assets"
    if (local_assets / "yolov8.xml").is_file():
        return local_assets
    if get_package_share_directory is not None:
        try:
            share_assets = Path(get_package_share_directory("sp_vision_25")) / "assets"
            if (share_assets / "yolov8.xml").is_file():
                return share_assets
        except Exception:
            pass
    return local_assets


def _stamp_ns(stamp) -> int:
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


def _quat_to_yaw_pitch(qx: float, qy: float, qz: float, qw: float) -> tuple[float, float]:
    """Convert ROS ENU quaternion (x, y, z, w) to (yaw_ccw_rad, pitch_down_pos_rad)."""
    siny_cosp = 2.0 * (qw * qz + qx * qy)
    cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
    yaw = math.atan2(siny_cosp, cosy_cosp)
    sinp = max(-1.0, min(1.0, 2.0 * (qw * qy - qz * qx)))
    # In standard ROS FLU body frame, +Y pitch rotation pitches nose DOWN (+pitch_down).
    pitch_down = math.asin(sinp)
    return yaw, pitch_down


def _attitude_at(feedback: deque[tuple[int, float, float]], stamp: int, max_gap_ns: int = 60_000_000) -> tuple[float, float] | None:
    before = next((s for s in reversed(feedback) if s[0] <= stamp), None)
    after = next((s for s in feedback if s[0] >= stamp), None)
    if before and after and stamp - before[0] <= max_gap_ns and after[0] - stamp <= max_gap_ns:
        if before[0] == after[0]:
            return before[1], before[2]
        t = (stamp - before[0]) / (after[0] - before[0])
        return (
            before[1] + t * math.remainder(after[1] - before[1], 2.0 * math.pi),
            before[2] + t * (after[2] - before[2]),
        )
    if feedback and abs(stamp - feedback[-1][0]) <= max_gap_ns:
        return feedback[-1][1], feedback[-1][2]
    return None


class SpVisionSimNode(Node):
    """Unified sp_vision_25 ROS 2 auto-aim node for UE5 simulation and real robot deployment."""

    def __init__(self) -> None:
        super().__init__("sp_vision_25_sim")
        self.robot_id = str(self.declare_parameter("robot_id", "aim_sentry").value)
        assets_dir = Path(str(self.declare_parameter("assets_dir", str(_default_assets_dir())).value))
        muzzle_v = float(self.declare_parameter("muzzle_speed_mps", 24.5).value)
        min_cls_conf = float(self.declare_parameter("min_classify_conf", 0.60).value)
        self.require_behavior_switch = bool(self.declare_parameter("require_behavior_switch", False).value)

        # Real-robot deployment & calibration parameters (defaults keep simulation behavior identical)
        self.enemy_color_override = str(self.declare_parameter("enemy_color", "auto").value).strip().lower()
        self.referee_mode = str(self.declare_parameter("referee_mode", "auto").value).strip().lower()
        self.pitch_up_positive = bool(self.declare_parameter("pitch_up_positive", False).value)
        self.debug_image_enabled = bool(self.declare_parameter("debug_image", False).value)
        self.max_image_age_ns = int(float(self.declare_parameter("max_image_age_ms", 180.0).value) * 1_000_000)

        latency_s = float(self.declare_parameter("latency_s", 0.08).value)
        shoot_tol_deg = float(self.declare_parameter("shoot_tol_deg", 5.5).value)
        structure_shoot_tol_deg = float(self.declare_parameter("structure_shoot_tol_deg", 8.5).value)
        yaw_offset_deg = float(self.declare_parameter("yaw_offset_deg", 0.0).value)
        pitch_offset_deg = float(self.declare_parameter("pitch_offset_deg", 0.0).value)
        cam_to_barrel_z_m = float(self.declare_parameter("cam_to_barrel_z_m", 0.0).value)

        camera_topic = str(self.declare_parameter("camera_topic", "").value).strip() or f"/{self.robot_id}/camera/image_raw"
        camera_info_topic = str(self.declare_parameter("camera_info_topic", "").value).strip() or f"/{self.robot_id}/camera/camera_info"
        joint_state_topic = str(self.declare_parameter("joint_state_topic", "").value).strip() or f"/{self.robot_id}/joint_states"
        imu_topic = str(self.declare_parameter("imu_topic", "").value).strip()
        command_topic = str(self.declare_parameter("command_topic", "").value).strip() or f"/{self.robot_id}/autoaim_command"
        amcl_topic = str(self.declare_parameter("amcl_topic", "/amcl_pose").value).strip() or "/amcl_pose"

        self.detector = SpVisionYoloV8Detector(assets_dir, min_classify_conf=min_cls_conf)
        self.tracker = WorldAngularTracker(max_lost_s=TRACK_HOLD_NS * 1e-9)
        self.aimer = BallisticAimer(
            muzzle_speed_mps=muzzle_v,
            latency_s=latency_s,
            shoot_tol_rad=math.radians(shoot_tol_deg),
            structure_shoot_tol_rad=math.radians(structure_shoot_tol_deg),
            yaw_offset_rad=math.radians(yaw_offset_deg),
            pitch_offset_rad=math.radians(pitch_offset_deg),
            cam_to_barrel_z_m=cam_to_barrel_z_m,
        )

        # When enemy_color is explicitly set (e.g. bench test), initialize team accordingly
        if self.enemy_color_override == "red":
            self.is_red = False
        else:
            self.is_red = True
        self.game_running = not self.require_behavior_switch
        self.robot_alive = True
        self.cur_yaw = 0.0
        self.cur_pitch = 0.0
        self.self_pos_m: tuple[float, float, float] | None = None
        self.enemy_world_xy: tuple[float, float] | None = None
        self.enemy_world_vel: tuple[float, float] = (0.0, 0.0)
        self.enemy_xy_stamp_s: float = 0.0
        self.K = np.array([[1108.5, 0.0, 640.0], [0.0, 1108.5, 360.0], [0.0, 0.0, 1.0]], dtype=np.float64)
        self.D = np.zeros((5,), dtype=np.float64)

        self.behavior_seen = False
        self.behavior_permits = not self.require_behavior_switch
        self.behavior_stamp = -1
        self.fire_permitted: bool | None = None
        self.fire_permit_stamp: int | None = None
        self.target_priority = "ENEMY_VEHICLE_FIRST"
        self.live_targets = set(VEHICLE_NAMES) | {"outpost", "base"}

        self.sim_state_seen = False
        self.sim_match = False
        self.sim_referee_seen = False
        self.sim_team_known = False
        self.last_sim_frame_stamp = -1
        self.sim_observation: PlateObservation | None = None
        self.sim_challenger: PlateObservation | None = None
        self.identity: tuple | None = None
        self.feedback: deque[tuple[int, float, float]] = deque(maxlen=128)
        self.pending_image: Image | None = None

        self.track_id = 0
        self.track_name: str | None = None
        self.track_stamp = 0
        self.track_visible = False
        self.last_solution: AimSolution | None = None
        self.command_firing = False
        self.permitted = False
        self.last_cmd_stamp = 0
        self.frames = 0
        self.tracked = 0
        self.trigger_frames = 0
        self.last_diagnostic = 0

        qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=5, reliability=ReliabilityPolicy.RELIABLE)
        img_qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)

        if self.referee_mode in ("auto", "sim"):
            self.create_subscription(String, "/rmsim/referee/state", self._on_referee, qos)
        if self.referee_mode in ("auto", "dji") and HAS_DJI_REFEREE:
            self.create_subscription(DjiGameStatus, "/referee/common/game_status", self._on_dji_game, qos)
            self.create_subscription(DjiRobotPerformance, "/referee/common/robot_performance", self._on_dji_perf, qos)
            self.create_subscription(DjiRobotHP, "/referee/common/robot_hp", self._on_dji_hp, qos)
            self.create_subscription(DjiSelfColor, "/referee/parsed/common/self_color", self._on_dji_color, qos)
            self.create_subscription(DjiShootData, "/referee/common/shoot_data", self._on_dji_shoot, qos)

        self.create_subscription(JointState, joint_state_topic, self._on_joints, qos_profile_sensor_data)
        if imu_topic:
            self.create_subscription(Imu, imu_topic, self._on_imu, qos_profile_sensor_data)
        self.create_subscription(CameraInfo, camera_info_topic, self._on_info, qos_profile_sensor_data)
        self.create_subscription(Image, camera_topic, self._on_image, img_qos)
        self.create_subscription(String, f"/rmsim/{self.robot_id}/state", self._on_state, 5)
        self.create_subscription(PoseWithCovarianceStamped, amcl_topic, self._on_amcl_pose, qos)
        self.create_subscription(Int32, f"/{self.robot_id}/behavior/auto_aim_switch", self._on_behavior, 5)
        self.create_subscription(Int32, f"/{self.robot_id}/behavior/fire_permit", self._on_fire_permit, 5)
        self.create_subscription(String, f"/{self.robot_id}/behavior/target_priority", self._on_priority, 5)

        self.cmd_pub = self.create_publisher(AutoAimCommandMsg, command_topic, 1) if HAS_SP_MSGS else None
        self.permit_pub = self.create_publisher(Int32, f"/{self.robot_id}/auto_aim_switch", 1)
        self.track_pub = self.create_publisher(String, f"/{self.robot_id}/behavior/vision_track", 1)
        self.status_pub = self.create_publisher(String, "/sp_vision/status", qos)
        self.debug_pub = self.create_publisher(Image, "/sp_vision/debug_image", img_qos) if self.debug_image_enabled else None
        self.create_timer(0.05, self._watchdog)

        self.get_logger().info(
            f"sp_vision_25 dual-engine world-tracker ready "
            f"(robot_id={self.robot_id}, sp_msgs={HAS_SP_MSGS}, dji_ref={HAS_DJI_REFEREE}, "
            f"v0={muzzle_v}m/s, enemy_color={self.enemy_color_override}, "
            f"pitch_up_pos={self.pitch_up_positive}, require_bt={self.require_behavior_switch})"
        )

    def _now_ns(self) -> int:
        return int(self.get_clock().now().nanoseconds)

    def _simulator_observations(self) -> bool:
        return self.referee_mode != "dji" and (
            self.sim_state_seen or self.sim_referee_seen or self.referee_mode == "sim"
        )

    def _enemy_color(self) -> str:
        if self.sim_team_known:
            return "blue" if self.is_red else "red"
        if self.enemy_color_override in ("red", "blue"):
            return self.enemy_color_override
        return "blue" if self.is_red else "red"

    def _behavior_active(self, now: int) -> bool:
        if not self.require_behavior_switch and not self.behavior_seen:
            return True
        return self.behavior_permits and 0 <= now - self.behavior_stamp <= BEHAVIOR_COMMAND_NS

    def _fire_gate_open(self, now: int) -> bool:
        if self.fire_permitted is None:
            return True
        return (
            self.fire_permitted
            and self.fire_permit_stamp is not None
            and 0 <= now - self.fire_permit_stamp <= BEHAVIOR_COMMAND_NS
        )

    def _allowed_names(self) -> set[str]:
        allowed = set(VEHICLE_NAMES)
        sec = SECONDARY_TARGETS.get(self.target_priority)
        if sec is not None:
            allowed.add(sec)
        return allowed & self.live_targets

    def _release(self) -> None:
        self.tracker.reset()
        self.track_name = None
        self.sim_observation = None
        self.sim_challenger = None
        self.track_visible = False
        self.last_solution = None
        self.enemy_world_xy = None
        self.enemy_world_vel = (0.0, 0.0)
        self.enemy_xy_stamp_s = 0.0
        self.command_firing = False
        if self.permitted:
            self.permit_pub.publish(Int32(data=0))
            self.permitted = False

    def _stop_firing(self, now: int) -> None:
        if (
            self.command_firing
            and self.last_solution is not None
            and self.track_stamp > 0
            and 0 <= now - self.track_stamp <= TRACK_HOLD_NS
        ):
            self._publish_cmd(self.last_solution, now, False)
        self.command_firing = False

    def _record_attitude_and_fast_fire(self, stamp_ns: int, world_yaw: float, world_pitch_down: float) -> None:
        self.cur_yaw = world_yaw
        self.cur_pitch = world_pitch_down
        self.feedback.append((int(stamp_ns), world_yaw, world_pitch_down))
        if self.pending_image is not None:
            self._process_image(self.pending_image)
        elif (
            self.track_visible
            and not self.command_firing
            and self.last_solution is not None
            and self.track_name is not None
        ):
            # High-rate fire-on-alignment: as soon as fast gimbal slews onto target, fire immediately
            # without waiting for the next camera frame.
            now = self._now_ns()
            if (
                0 <= now - self.track_stamp <= MATCH_FIRE_OBSERVATION_NS
                and self.game_running
                and self.robot_alive
                and self._behavior_active(now)
                and self._fire_gate_open(now)
            ):
                is_struct = self.track_name in ("outpost", "base")
                min_hits = 1 if is_struct else 2
                tol = self.aimer.structure_shoot_tol_rad if is_struct else self.aimer.shoot_tol_rad
                ey = abs(math.remainder(self.last_solution.target_yaw - world_yaw, 2.0 * math.pi))
                ep = abs(self.last_solution.target_pitch - world_pitch_down)
                if self.tracker.track_hits >= min_hits and ey <= tol and ep <= tol:
                    self._publish_cmd(self.last_solution, now, True)

    # --- Simulator JSON referee callback ---
    def _on_referee(self, msg: String) -> None:
        try:
            ref = json.loads(msg.data)
        except Exception:
            return
        if not isinstance(ref, dict) or ref.get("available") is False:
            return
        self.sim_referee_seen = True
        gs = ref.get("game_status") or {}
        rs = ref.get("robot_status") or {}
        if "game_progress" in gs:
            self.game_running = int(gs["game_progress"]) == 4
        team_red = ref.get("friendly_red")
        if type(team_red) is not bool:
            robot_id = rs.get("robot_id")
            team_red = robot_id < 100 if type(robot_id) is int and 0 < robot_id < 200 else None
        if team_red is not None:
            if not self.sim_team_known or team_red != self.is_red:
                self._release()
            self.is_red = team_red
            self.sim_team_known = True
        if "current_hp" in rs:
            self.robot_alive = int(rs["current_hp"]) > 0

        enemy = self._enemy_color()
        field_devices = ref.get("field_devices") or {}
        enemy_site = field_devices.get(enemy) or {}
        # The HP map does not distinguish an absent class from an eliminated vehicle.
        live = set(VEHICLE_NAMES)
        outposts = (field_devices.get(site) or {} for site in ("red", "blue"))
        if any(site.get("outpost_hp", 0) > 0 and site.get("outpost_owner_red") is (not self.is_red) for site in outposts):
            live.add("outpost")
        if enemy_site.get("base_hp", 0) > 0 and enemy_site.get("base_protected") is False:
            live.add("base")
        self.live_targets = live

        if not self.robot_alive or not self.game_running or (
            self.track_name is not None and self.track_name not in self._allowed_names()
        ):
            self._release()

    # --- Real-robot native dji_referee_protocol callbacks ---
    def _on_dji_game(self, msg) -> None:
        if self.sim_referee_seen:
            return
        self.game_running = int(msg.game_progress) == 4
        if not self.game_running:
            self._release()

    def _on_dji_color(self, msg) -> None:
        if self.sim_referee_seen or self.enemy_color_override != "auto":
            return
        color = int(msg.color)
        if color in (1, 2):
            is_red = color == 1
            if is_red != self.is_red:
                self._release()
            self.is_red = is_red

    def _on_dji_perf(self, msg) -> None:
        if self.sim_referee_seen:
            return
        rid = int(msg.robot_id)
        if rid > 0 and self.enemy_color_override == "auto":
            is_red = rid < 100
            if is_red != self.is_red:
                self._release()
            self.is_red = is_red
        self.robot_alive = int(msg.current_hp) > 0
        if not self.robot_alive:
            self._release()

    def _on_dji_hp(self, msg) -> None:
        if self.sim_referee_seen:
            return
        enemy = self._enemy_color()
        live: set[str] = set()
        for vname, suffix in VEHICLE_HP_KEYS.items():
            attr = f"{enemy}_{suffix}"
            if hasattr(msg, attr):
                if int(getattr(msg, attr, 0)) > 0:
                    live.add(vname)
            elif vname == "five":
                # 2026 protocol RobotHP omits 5_robot_hp; keep 5 allowed so real 5-number armors stay lockable
                live.add(vname)
        if int(getattr(msg, f"{enemy}_outpost_hp", 0)) > 0:
            live.add("outpost")
        if int(getattr(msg, f"{enemy}_base_hp", 0)) > 0:
            live.add("base")
        self.live_targets = live

    def _on_dji_shoot(self, msg) -> None:
        # Continuously adapt ballistic muzzle velocity from real referee 0x0207 shoot_data
        speed = float(getattr(msg, "initial_speed", 0.0))
        if 18.0 <= speed <= 29.5:
            self.aimer.v0 = 0.75 * self.aimer.v0 + 0.25 * speed

    def _on_behavior(self, msg: Int32) -> None:
        self.behavior_seen = True
        self.behavior_permits = msg.data == 1
        self.behavior_stamp = self._now_ns()
        if not self.behavior_permits:
            self._release()

    def _on_fire_permit(self, msg: Int32) -> None:
        self.fire_permitted = msg.data == 1
        self.fire_permit_stamp = self._now_ns()
        if not self.fire_permitted:
            self._stop_firing(self.fire_permit_stamp)

    def _on_priority(self, msg: String) -> None:
        if msg.data in SECONDARY_TARGETS:
            self.target_priority = msg.data
            if self.track_name is not None and self.track_name not in self._allowed_names():
                self._release()

    def _on_amcl_pose(self, msg: PoseWithCovarianceStamped) -> None:
        p = msg.pose.pose.position
        self.self_pos_m = (float(p.x), float(p.y), float(p.z))

    def _on_state(self, msg: String) -> None:
        try:
            d = json.loads(msg.data)
        except ValueError:
            self._release()
            return
        if not isinstance(d, dict):
            self._release()
            return
        self.sim_state_seen = True
        aim = d.get("aim") or {}
        identity = (d.get("session"), d.get("epoch"))
        if identity != self.identity:
            self.identity = identity
            self.fire_permit_stamp = None
            self.last_cmd_stamp = 0
            self.feedback.clear()
            self.pending_image = None
            if self._simulator_observations():
                self.last_sim_frame_stamp = -1
                self.sim_team_known = False
            self._release()
        self.sim_match = bool(
            d.get("state_fresh")
            and d.get("drive", {}).get("match_autonomy")
            and aim.get("enabled")
            and "match_fire" in aim
        )
        if self.sim_match and "stamp_ns" in aim:
            cur_y = float(aim["world_yaw_rad"])
            cur_p = float(aim["world_pitch_rad"])
            self._record_attitude_and_fast_fire(int(aim["stamp_ns"]), cur_y, cur_p)
        elif not self.sim_match:
            self._release()

    def _on_joints(self, msg: JointState) -> None:
        yaw = self.cur_yaw
        pitch_down = self.cur_pitch
        updated = False
        for name, pos in zip(msg.name, msg.position):
            low = name.lower()
            if "yaw" in low:
                yaw = float(pos)
                updated = True
            elif "pitch" in low:
                raw_p = float(pos)
                pitch_down = -raw_p if self.pitch_up_positive else raw_p
                updated = True
        if updated and not self.sim_state_seen:
            stamp = _stamp_ns(msg.header.stamp)
            if stamp <= 0:
                stamp = self._now_ns()
            self._record_attitude_and_fast_fire(stamp, yaw, pitch_down)
        elif updated:
            self.cur_yaw = yaw
            self.cur_pitch = pitch_down

    def _on_imu(self, msg: Imu) -> None:
        if self.sim_state_seen:
            return
        q = msg.orientation
        yaw, pitch_down = _quat_to_yaw_pitch(float(q.x), float(q.y), float(q.z), float(q.w))
        stamp = _stamp_ns(msg.header.stamp)
        if stamp <= 0:
            stamp = self._now_ns()
        self._record_attitude_and_fast_fire(stamp, yaw, pitch_down)

    def _on_info(self, msg: CameraInfo) -> None:
        if len(msg.k) == 9 and msg.k[0] > 1.0:
            self.K = np.array(msg.k, dtype=np.float64).reshape(3, 3)
        if len(msg.d) >= 4:
            self.D = np.array(msg.d, dtype=np.float64)

    def _pick_target(
        self, detections: list[ArmorDetection], cur_yaw: float, cur_pitch: float, stamp_ns: int
    ) -> ArmorDetection | None:
        enemy_color = self._enemy_color()
        allowed = self._allowed_names()
        enemies = [d for d in detections if d.color == enemy_color and d.name in allowed]
        if not enemies:
            return None
        fx, fy, cx, cy = float(self.K[0, 0]), float(self.K[1, 1]), float(self.K[0, 2]), float(self.K[1, 2])

        def observe(d: ArmorDetection) -> PlateObservation:
            yaw, pitch = ray_to_world_angles(cur_yaw, cur_pitch, (d.center[0] - cx) / fx, (d.center[1] - cy) / fy)
            return PlateObservation(d.name, yaw, pitch, stamp_ns)

        def score(d: ArmorDetection, observation: PlateObservation | None):
            ang_pen = 0.0
            if self.tracker.initialized:
                assert observation is not None
                ang_pen = -math.hypot(
                    math.remainder(observation.yaw - self.tracker.yaw, 2.0 * math.pi),
                    observation.pitch - self.tracker.pitch,
                )
            return (d.name in VEHICLE_NAMES, TARGET_PRIORITY.get(d.name, 0), ang_pen, d.confidence)

        simulate = self._simulator_observations()
        if not simulate:
            return max(enemies, key=lambda d: score(d, observe(d) if self.tracker.initialized else None))

        observations = [(d, observe(d)) for d in enemies]
        if self.sim_observation is None:
            best, self.sim_observation = max(observations, key=lambda pair: score(*pair))
            return best

        lost_ns = stamp_ns - self.track_stamp
        limit = 0.11 + min(0.11, max(0.0, 0.55e-9 * lost_ns))
        continued = None
        for d, observation in observations:
            separation = observation.separation(self.sim_observation)
            if observation.name == self.sim_observation.name and separation < limit:
                continued = d, observation
                limit = separation
        if continued is not None:
            best, self.sim_observation = continued
            self.sim_challenger = None
            return best

        # A different plate must recur after the current observation disappears.
        best, observation = max(observations, key=lambda pair: score(*pair))
        previous = self.sim_challenger
        if (
            previous is not None and previous.name == observation.name
            and stamp_ns - previous.stamp_ns <= SIM_HANDOFF_GAP_NS
            and observation.separation(previous) < 0.14
        ):
            observation.count = previous.count + 1
        self.sim_challenger = observation
        if observation.count < 3 or lost_ns < SIM_HANDOFF_HOLD_NS:
            return None

        self._release()
        self.sim_observation = observation
        return best

    def _publish_cmd(self, sol: AimSolution, stamp_ns: int, shoot: bool) -> None:
        if self.cmd_pub is None:
            return
        safe_stamp = max(int(stamp_ns), self.last_cmd_stamp)
        self.last_cmd_stamp = safe_stamp
        cmd = AutoAimCommandMsg()
        if hasattr(cmd, "timestamp"):
            cmd.timestamp.sec, cmd.timestamp.nanosec = divmod(safe_stamp, 1_000_000_000)
        cmd.control = bool(sol.locked)
        cmd.shoot = bool(shoot)
        cmd.yaw = float(sol.target_yaw)
        cmd.pitch = float(-sol.target_pitch if self.pitch_up_positive else sol.target_pitch)
        self.permit_pub.publish(Int32(data=1))
        self.permitted = True
        self.command_firing = cmd.shoot
        self.trigger_frames += int(cmd.shoot)
        self.cmd_pub.publish(cmd)

    def _publish_debug_image(
        self,
        bgr: np.ndarray,
        dets: list[ArmorDetection],
        best: ArmorDetection | None,
        sol: AimSolution | None,
        can_shoot: bool,
        header,
    ) -> None:
        if self.debug_pub is None:
            return
        vis = bgr.copy()
        for d in dets:
            col = (0, 255, 0) if d is best else ((255, 160, 0) if d.color == "blue" else (0, 80, 255))
            pts = d.keypoints.astype(np.int32).reshape(-1, 1, 2)
            cv2.polylines(vis, [pts], True, col, 2)
            cv2.putText(
                vis,
                f"{d.color[0].upper()}:{d.name} {d.confidence:.2f}",
                (max(0, d.box[0]), max(18, d.box[1] - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                col,
                2,
            )
        if sol is not None:
            txt = (
                f"LOCK {self.track_name} d={sol.distance_m:.2f}m v0={self.aimer.v0:.1f} "
                f"err=({math.degrees(sol.err_yaw):+.1f},{math.degrees(sol.err_pitch):+.1f})deg "
                f"FIRE={'ON' if can_shoot else 'OFF'}"
            )
            cv2.putText(vis, txt, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255) if can_shoot else (220, 220, 220), 2)
        out = Image()
        out.header = header
        out.height, out.width = vis.shape[:2]
        out.encoding = "bgr8"
        out.step = out.width * 3
        out.data = vis.tobytes()
        self.debug_pub.publish(out)

    def _watchdog(self) -> None:
        now = self._now_ns()
        if (self.require_behavior_switch or self.behavior_seen) and self.behavior_permits:
            if not 0 <= now - self.behavior_stamp <= BEHAVIOR_COMMAND_NS:
                self.behavior_permits = False
                self._release()
        if self.track_name is not None:
            age = now - self.track_stamp
            if not 0 <= age <= TRACK_HOLD_NS or self.track_name not in self._allowed_names():
                self._release()
            elif age > MATCH_FIRE_OBSERVATION_NS:
                self.track_visible = False
                if self.last_solution is not None and self._behavior_active(now) and self.game_running and self.robot_alive:
                    self._publish_cmd(self.last_solution, now, False)
        if self.command_firing and not (
            self.track_visible
            and self.game_running
            and self.robot_alive
            and self._behavior_active(now)
            and self._fire_gate_open(now)
        ):
            self._stop_firing(now)

        label_id = ARMOR_LABEL_IDS.get(self.track_name) if self.track_name else None
        self.track_pub.publish(
            String(
                data=json.dumps(
                    {
                        "stamp_ns": now,
                        "session": self.identity[0] if self.identity else None,
                        "epoch": self.identity[1] if self.identity else None,
                        "track_id": self.track_id if self.track_name else None,
                        "label": label_id,
                        "observation_stamp_ns": self.track_stamp if self.track_name else None,
                        "visible": self.track_visible,
                        "firing": self.command_firing,
                        "fire_permit": self._fire_gate_open(now) if self.fire_permitted is not None else None,
                        "target_priority": self.target_priority,
                        "distance_m": round(self.last_solution.distance_m, 2) if self.last_solution else None,
                        "self_x": round(self.self_pos_m[0], 2) if self.self_pos_m else None,
                        "self_y": round(self.self_pos_m[1], 2) if self.self_pos_m else None,
                        "enemy_x": round(self.enemy_world_xy[0], 2) if self.enemy_world_xy else None,
                        "enemy_y": round(self.enemy_world_xy[1], 2) if self.enemy_world_xy else None,
                        "enemy_vx": round(self.enemy_world_vel[0], 2) if self.enemy_world_xy else 0.0,
                        "enemy_vy": round(self.enemy_world_vel[1], 2) if self.enemy_world_xy else 0.0,
                    },
                    separators=(",", ":"),
                )
            )
        )
        if now - self.last_diagnostic >= 2_000_000_000:
            self.last_diagnostic = now
            self.get_logger().info(
                f"sp_vision_25 frames={self.frames} tracked={self.tracked} "
                f"trigger={self.trigger_frames} permitted={self.permitted} "
                f"v0={self.aimer.v0:.1f} target={self.track_name or 'none'}"
            )

    def _on_image(self, msg: Image) -> None:
        self.frames += 1
        self.pending_image = msg
        self._process_image(msg)

    def _process_image(self, msg: Image) -> None:
        now = self._now_ns()
        simulate = self._simulator_observations()
        if not self.game_running or not self.robot_alive or not self._behavior_active(now) or (
            simulate and not self.sim_team_known
        ):
            return
        if msg.height <= 0 or msg.width <= 0:
            return

        stamp = _stamp_ns(msg.header.stamp)
        if stamp <= 0:
            stamp = now

        if self.sim_state_seen:
            if not self.sim_match or not self.feedback:
                return
            if now < stamp:
                return
            if now - stamp > self.max_image_age_ns:
                self.pending_image = None
                return
            sample = _attitude_at(self.feedback, stamp)
            if sample is None:
                return
            cur_yaw, cur_pitch = sample
        else:
            if self.feedback:
                sample = _attitude_at(self.feedback, stamp)
                cur_yaw, cur_pitch = sample if sample is not None else (self.feedback[-1][1], self.feedback[-1][2])
            else:
                cur_yaw, cur_pitch = self.cur_yaw, self.cur_pitch

        self.pending_image = None
        if (self.track_name is not None and stamp < self.track_stamp) or (
            simulate and stamp <= self.last_sim_frame_stamp
        ):
            return

        arr = np.frombuffer(msg.data, dtype=np.uint8)
        ch = 3 if msg.encoding in ("rgb8", "bgr8") else (4 if msg.encoding in ("rgba8", "bgra8") else 0)
        if ch == 0 or msg.step < msg.width * ch or arr.size < msg.height * msg.step:
            return
        img = arr[: msg.height * msg.step].reshape((msg.height, msg.step))[:, : msg.width * ch].reshape((msg.height, msg.width, ch))
        bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if msg.encoding.startswith("rgb") else img[:, :, :3]

        enemy_color = self._enemy_color()
        t_sec = stamp * 1e-9 if stamp > 0 else time.monotonic()
        prefer_outpost = self.target_priority == "ENEMY_VEHICLE_THEN_OUTPOST"
        if simulate:
            self.last_sim_frame_stamp = stamp
            if self.track_name is not None and stamp - self.track_stamp > TRACK_HOLD_NS:
                self._release()
        dets = self.detector.detect(
            bgr, target_color=enemy_color, prefer_outpost=prefer_outpost, verify_sim_lamp_color=simulate
        )
        best = self._pick_target(dets, cur_yaw, cur_pitch, stamp)

        meas = None
        active_name = best.name if best is not None else self.track_name
        target_kind = "outpost" if active_name == "outpost" else ("base" if active_name == "base" else "vehicle")
        is_structure = target_kind in ("outpost", "base")
        if best is not None:
            fx, fy, cx, cy = float(self.K[0, 0]), float(self.K[1, 1]), float(self.K[0, 2]), float(self.K[1, 2])
            pnp = solve_armor_pnp(best.keypoints, best.armor_type, self.K, dist_coeffs=self.D)
            if pnp is not None:
                dist = float(np.linalg.norm(pnp))
                u = float(pnp[0]) / max(0.1, float(pnp[2]))
                v = float(pnp[1]) / max(0.1, float(pnp[2]))
            else:
                u = (best.center[0] - cx) / fx
                v = (best.center[1] - cy) / fy
                bar_h = max(4.0, best.bar_height_px)
                dist = max(0.5, min(16.0, 0.056 * fy / bar_h))
            wyaw, wpitch = ray_to_world_angles(cur_yaw, cur_pitch, u, v)
            meas = (wyaw, wpitch, dist)

        tracked = self.tracker.update(meas, t_sec, target_kind=target_kind)
        if tracked is None:
            self.track_visible = False
            self._stop_firing(now)
            self._publish_debug_image(bgr, dets, None, None, False, msg.header)
            return

        wyaw, wpitch, dist, yr, pr, hits = tracked
        if best is not None:
            if self.track_name != best.name:
                self.track_id += 1
            self.track_name = best.name
            self.track_stamp = stamp
            self.track_visible = True
            self.tracked += 1
        else:
            self.track_visible = False

        # Compare against the LATEST gimbal angle (not the older capture-time angle) for tight firing accuracy
        eval_yaw, eval_pitch = (self.feedback[-1][1], self.feedback[-1][2]) if self.feedback else (cur_yaw, cur_pitch)
        sol = self.aimer.solve_world(wyaw, wpitch, dist, yr, pr, eval_yaw, eval_pitch, hits, target_kind=target_kind)
        self.last_solution = sol
        if self.self_pos_m is not None and not is_structure and hits >= 3 and sol.distance_m <= 12.0:
            horiz = sol.distance_m * math.cos(sol.target_pitch)
            new_ex = self.self_pos_m[0] + horiz * math.cos(sol.target_yaw)
            new_ey = self.self_pos_m[1] + horiz * math.sin(sol.target_yaw)
            if self.enemy_world_xy is not None and 0.03 <= (t_sec - self.enemy_xy_stamp_s) <= 0.6:
                dt_xy = t_sec - self.enemy_xy_stamp_s
                raw_vx = max(-4.0, min(4.0, (new_ex - self.enemy_world_xy[0]) / dt_xy))
                raw_vy = max(-4.0, min(4.0, (new_ey - self.enemy_world_xy[1]) / dt_xy))
                self.enemy_world_vel = (
                    0.65 * self.enemy_world_vel[0] + 0.35 * raw_vx,
                    0.65 * self.enemy_world_vel[1] + 0.35 * raw_vy,
                )
            self.enemy_world_xy = (new_ex, new_ey)
            self.enemy_xy_stamp_s = t_sec
        elif is_structure:
            self.enemy_world_xy = None
            self.enemy_world_vel = (0.0, 0.0)
        can_shoot = bool(
            sol.shoot
            and self.track_visible
            and self.game_running
            and self.robot_alive
            and self._behavior_active(now)
            and self._fire_gate_open(now)
        )
        cmd_stamp = max(stamp, now - 20_000_000) if self.sim_state_seen else stamp
        self._publish_cmd(sol, cmd_stamp, can_shoot)
        self._publish_debug_image(bgr, dets, best, sol, can_shoot, msg.header)

        st = String()
        st.data = json.dumps(
            {
                "target": self.track_name or "tracked",
                "color": best.color if best else enemy_color,
                "distance_m": round(sol.distance_m, 2),
                "tof_s": round(sol.tof_s, 3),
                "yaw_deg": round(math.degrees(sol.target_yaw), 2),
                "pitch_deg": round(math.degrees(sol.target_pitch), 2),
                "shoot": can_shoot,
                "bt_permit": self._behavior_active(now),
                "fire_permit": self._fire_gate_open(now),
            }
        )
        self.status_pub.publish(st)


# Clean alias for real-robot callers
SpVisionNode = SpVisionSimNode


def main(args=None) -> None:
    rclpy.init(args=args)
    node = SpVisionSimNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node._release()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
