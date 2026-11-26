"""ROS 2 sp_vision_25 Auto-Aim node for RMUC 2027 Simulator & Real Robot.

Pipeline:
  Dual-engine (OpenVINO YOLOv8 + full-res 1280x720 HSV lightbar & outpost sticker + tiny_resnet)
  -> PnP / lightbar 3D range -> Inertial rmsim_world angular tracker (immune to gimbal spin)
  -> 17mm (24.5 m/s) gravity drop + world angular lead -> sp_msgs/AutoAimCommandMsg.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from collections import deque
from pathlib import Path

_DLL_HANDLES = []
_ws = Path(__file__).resolve().parents[3]
for _pkg in ("sp_msgs", "pb_rm_interfaces"):
    _bin = _ws / "install_control" / _pkg / "bin"
    if _bin.is_dir():
        _bin_str = str(_bin)
        if hasattr(os, "add_dll_directory"):
            _DLL_HANDLES.append(os.add_dll_directory(_bin_str))
        if _bin_str not in os.environ.get("PATH", ""):
            os.environ["PATH"] = _bin_str + os.pathsep + os.environ.get("PATH", "")
    _site = _ws / "install_control" / _pkg / "Lib" / "site-packages"
    if _site.is_dir() and str(_site) not in sys.path:
        sys.path.insert(0, str(_site))

try:
    from rmsim_bridge.native_transport import load_windows_fastrtps
    load_windows_fastrtps()
except Exception:
    pass

try:
    from rmsim_bridge.control_dlls import load_package_dlls
    load_package_dlls("sp_msgs", "pb_rm_interfaces")
except Exception:
    pass

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from geometry_msgs.msg import PoseWithCovarianceStamped
from sensor_msgs.msg import CameraInfo, Image, JointState
from std_msgs.msg import Int32, String

try:
    from sp_msgs.msg import AutoAimCommandMsg
    HAS_SP_MSGS = True
except ImportError:
    HAS_SP_MSGS = False

try:
    from rmsim_bridge.protocol import MATCH_FIRE_OBSERVATION_NS, MATCH_TRACK_MEMORY_NS
except Exception:
    MATCH_FIRE_OBSERVATION_NS = 150_000_000
    MATCH_TRACK_MEMORY_NS = 350_000_000

from .detector import ArmorDetection, SpVisionYoloV8Detector
from .solver_tracker import AimSolution, BallisticAimer, WorldAngularTracker, ray_to_world_angles, solve_armor_pnp

BEHAVIOR_COMMAND_NS = 300_000_000
TRACK_HOLD_NS = 1_100_000_000  # Keep gimbal locked on predicted world angle for 1.1s during brief occlusion/face switch

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


def _stamp_ns(stamp) -> int:
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


def _attitude_at(feedback: deque[tuple[int, float, float]], stamp: int) -> tuple[float, float] | None:
    before = next((s for s in reversed(feedback) if s[0] <= stamp), None)
    after = next((s for s in feedback if s[0] >= stamp), None)
    if before and after and stamp - before[0] <= 60_000_000 and after[0] - stamp <= 60_000_000:
        if before[0] == after[0]:
            return before[1], before[2]
        t = (stamp - before[0]) / (after[0] - before[0])
        return (
            before[1] + t * math.remainder(after[1] - before[1], 2.0 * math.pi),
            before[2] + t * (after[2] - before[2]),
        )
    # Fallback to latest sample if within 60ms so fast frames aren't dropped
    if feedback and abs(stamp - feedback[-1][0]) <= 60_000_000:
        return feedback[-1][1], feedback[-1][2]
    return None


class SpVisionSimNode(Node):
    def __init__(self) -> None:
        super().__init__("sp_vision_25_sim")
        self.robot_id = str(self.declare_parameter("robot_id", "aim_sentry").value)
        default_assets = str(Path(__file__).resolve().parents[1] / "assets")
        assets_dir = Path(str(self.declare_parameter("assets_dir", default_assets).value))
        muzzle_v = float(self.declare_parameter("muzzle_speed_mps", 24.5).value)
        min_cls_conf = float(self.declare_parameter("min_classify_conf", 0.60).value)
        self.require_behavior_switch = bool(self.declare_parameter("require_behavior_switch", False).value)

        self.detector = SpVisionYoloV8Detector(assets_dir, min_classify_conf=min_cls_conf)
        self.tracker = WorldAngularTracker(max_lost_s=TRACK_HOLD_NS * 1e-9)
        self.aimer = BallisticAimer(muzzle_speed_mps=muzzle_v)

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

        self.behavior_seen = False
        self.behavior_permits = not self.require_behavior_switch
        self.behavior_stamp = -1
        self.fire_permitted: bool | None = None
        self.fire_permit_stamp: int | None = None
        self.target_priority = "ENEMY_VEHICLE_FIRST"
        self.live_targets = set(VEHICLE_NAMES) | {"outpost", "base"}

        self.sim_state_seen = False
        self.sim_match = False
        self.identity: tuple | None = None
        self.feedback: deque[tuple[int, float, float]] = deque(maxlen=48)
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
        self.create_subscription(String, "/rmsim/referee/state", self._on_referee, qos)
        self.create_subscription(JointState, f"/{self.robot_id}/joint_states", self._on_joints, qos_profile_sensor_data)
        self.create_subscription(CameraInfo, f"/{self.robot_id}/camera/camera_info", self._on_info, qos_profile_sensor_data)
        self.create_subscription(Image, f"/{self.robot_id}/camera/image_raw", self._on_image, img_qos)
        self.create_subscription(String, f"/rmsim/{self.robot_id}/state", self._on_state, 5)
        self.create_subscription(PoseWithCovarianceStamped, "/amcl_pose", self._on_amcl_pose, qos)
        self.create_subscription(Int32, f"/{self.robot_id}/behavior/auto_aim_switch", self._on_behavior, 5)
        self.create_subscription(Int32, f"/{self.robot_id}/behavior/fire_permit", self._on_fire_permit, 5)
        self.create_subscription(String, f"/{self.robot_id}/behavior/target_priority", self._on_priority, 5)

        self.cmd_pub = self.create_publisher(AutoAimCommandMsg, f"/{self.robot_id}/autoaim_command", 1) if HAS_SP_MSGS else None
        self.permit_pub = self.create_publisher(Int32, f"/{self.robot_id}/auto_aim_switch", 1)
        self.track_pub = self.create_publisher(String, f"/{self.robot_id}/behavior/vision_track", 1)
        self.status_pub = self.create_publisher(String, "/sp_vision/status", qos)
        self.create_timer(0.05, self._watchdog)

        self.get_logger().info(
            f"sp_vision_25 dual-engine world-tracker ready "
            f"(robot_id={self.robot_id}, sp_msgs={HAS_SP_MSGS}, v0={muzzle_v}m/s, "
            f"require_bt={self.require_behavior_switch})"
        )

    def _now_ns(self) -> int:
        return int(self.get_clock().now().nanoseconds)

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

    def _on_referee(self, msg: String) -> None:
        try:
            ref = json.loads(msg.data)
        except Exception:
            return
        gs = ref.get("game_status") or {}
        rs = ref.get("robot_status") or {}
        if "game_progress" in gs:
            self.game_running = int(gs["game_progress"]) == 4
        if "robot_id" in rs:
            is_red = int(rs["robot_id"]) < 100
            if is_red != self.is_red:
                self._release()
            self.is_red = is_red
        if "current_hp" in rs:
            self.robot_alive = int(rs["current_hp"]) > 0

        enemy = "blue" if self.is_red else "red"
        field_devices = ref.get("field_devices") or {}
        enemy_site = field_devices.get(enemy) or {}
        enemy_hp = ref.get("all_robot_hp") or {}
        # Enable every enemy vehicle (1-5 & 7 sentry) that is fielded and alive per legal referee all_robot_hp!
        live: set[str] = set()
        if enemy_hp:
            for vname, suffix in VEHICLE_HP_KEYS.items():
                if int(enemy_hp.get(f"{enemy}_{suffix}", 0)) > 0:
                    live.add(vname)
        else:
            live = set(VEHICLE_NAMES)
        outposts = (field_devices.get(site) or {} for site in ("red", "blue"))
        if any(site.get("outpost_hp", 0) > 0 and site.get("outpost_owner_red") is (not self.is_red) for site in outposts):
            live.add("outpost")
        if enemy_site.get("base_hp", 0) > 0 and enemy_site.get("base_protected") is False:
            live.add("base")
        self.live_targets = live

        if not self.robot_alive or not self.game_running:
            self._release()

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
            self.feedback.append((int(aim["stamp_ns"]), cur_y, cur_p))
            if self.pending_image is not None:
                self._process_image(self.pending_image)
            elif (
                self.track_visible
                and not self.command_firing
                and self.last_solution is not None
                and self.track_name is not None
            ):
                # High-rate fire-on-alignment: as soon as fast gimbal slews onto target, fire immediately
                # without waiting 100 ms for the next camera frame!
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
                    ey = abs(math.remainder(self.last_solution.target_yaw - cur_y, 2.0 * math.pi))
                    ep = abs(self.last_solution.target_pitch - cur_p)
                    if self.tracker.track_hits >= min_hits and ey <= tol and ep <= tol:
                        self._publish_cmd(self.last_solution, now, True)
        elif not self.sim_match:
            self._release()

    def _on_joints(self, msg: JointState) -> None:
        for name, pos in zip(msg.name, msg.position):
            low = name.lower()
            if "yaw" in low:
                self.cur_yaw = float(pos)
            elif "pitch" in low:
                self.cur_pitch = float(pos)

    def _on_info(self, msg: CameraInfo) -> None:
        if len(msg.k) == 9 and msg.k[0] > 1.0:
            self.K = np.array(msg.k, dtype=np.float64).reshape(3, 3)

    def _pick_target(self, detections: list[ArmorDetection], cur_yaw: float, cur_pitch: float) -> ArmorDetection | None:
        enemy_color = "blue" if self.is_red else "red"
        allowed = self._allowed_names()
        enemies = [d for d in detections if d.color == enemy_color and d.name in allowed]
        if not enemies:
            return None
        # Prefer vehicles over structures; among same class prefer closest to current lock
        fx, fy, cx, cy = float(self.K[0, 0]), float(self.K[1, 1]), float(self.K[0, 2]), float(self.K[1, 2])
        def score(d: ArmorDetection):
            is_veh = 1 if d.name in VEHICLE_NAMES else 0
            ang_pen = 0.0
            if self.tracker.initialized:
                wy, wp = ray_to_world_angles(cur_yaw, cur_pitch, (d.center[0] - cx) / fx, (d.center[1] - cy) / fy)
                ang_pen = -math.hypot(math.remainder(wy - self.tracker.yaw, 2.0 * math.pi), wp - self.tracker.pitch)
            return (is_veh, TARGET_PRIORITY.get(d.name, 0), ang_pen, d.confidence)
        return max(enemies, key=score)

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
        cmd.pitch = float(sol.target_pitch)
        self.permit_pub.publish(Int32(data=1))
        self.permitted = True
        self.command_firing = cmd.shoot
        self.trigger_frames += int(cmd.shoot)
        self.cmd_pub.publish(cmd)

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
                f"target={self.track_name or 'none'}"
            )

    def _on_image(self, msg: Image) -> None:
        self.frames += 1
        self.pending_image = msg
        self._process_image(msg)

    def _process_image(self, msg: Image) -> None:
        now = self._now_ns()
        if not self.game_running or not self.robot_alive or not self._behavior_active(now):
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
            if now - stamp > 180_000_000:
                self.pending_image = None
                return
            sample = _attitude_at(self.feedback, stamp)
            if sample is None:
                return
            cur_yaw, cur_pitch = sample
        else:
            cur_yaw, cur_pitch = self.cur_yaw, self.cur_pitch

        self.pending_image = None
        if self.track_name is not None and stamp < self.track_stamp:
            return

        arr = np.frombuffer(msg.data, dtype=np.uint8)
        ch = 3 if msg.encoding in ("rgb8", "bgr8") else (4 if msg.encoding in ("rgba8", "bgra8") else 0)
        if ch == 0 or msg.step < msg.width * ch or arr.size < msg.height * msg.step:
            return
        img = arr[: msg.height * msg.step].reshape((msg.height, msg.step))[:, : msg.width * ch].reshape((msg.height, msg.width, ch))
        bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if msg.encoding.startswith("rgb") else img[:, :, :3]

        enemy_color = "blue" if self.is_red else "red"
        t_sec = stamp * 1e-9 if stamp > 0 else time.monotonic()
        prefer_outpost = self.target_priority == "ENEMY_VEHICLE_THEN_OUTPOST"
        dets = self.detector.detect(bgr, target_color=enemy_color, prefer_outpost=prefer_outpost)
        best = self._pick_target(dets, cur_yaw, cur_pitch)

        meas = None
        active_name = best.name if best is not None else self.track_name
        target_kind = "outpost" if active_name == "outpost" else ("base" if active_name == "base" else "vehicle")
        is_structure = target_kind in ("outpost", "base")
        if best is not None:
            fx, fy, cx, cy = float(self.K[0, 0]), float(self.K[1, 1]), float(self.K[0, 2]), float(self.K[1, 2])
            pnp = solve_armor_pnp(best.keypoints, best.armor_type, self.K)
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
        if self.self_pos_m is not None and not is_structure and hits >= 3 and sol.distance_m <= 8.0:
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
