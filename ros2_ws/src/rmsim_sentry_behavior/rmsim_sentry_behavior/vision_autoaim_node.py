"""Decoupled ROS 2 Vision Auto-Aim node for RMUC 2027 Sentry (sp_vision_25 compatible).

Subscribes:
  - /{robot_id}/camera/image_raw    (sensor_msgs/Image)
  - /{robot_id}/camera/camera_info  (sensor_msgs/CameraInfo)
  - /rmsim/referee/state            (std_msgs/String JSON -> own team color & match state)
  - /{robot_id}/behavior/target_priority (std_msgs/String)
  - /{robot_id}/behavior/fire_permit (std_msgs/Int32)
Publishes:
  - /{robot_id}/autoaim_command    (simulator-only command consumed by rmsim_bridge)
  - /{robot_id}/behavior/vision_track (std_msgs/String JSON)
"""
from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from rmsim_bridge.native_transport import load_windows_fastrtps
load_windows_fastrtps()

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image
from std_msgs.msg import Int32, String
import cv2
from rmsim_bridge.control_dlls import load_package_dlls
from rmsim_bridge.protocol import MATCH_FIRE_OBSERVATION_NS, MATCH_TRACK_MEMORY_NS

load_package_dlls("sp_msgs")
from sp_msgs.msg import AutoAimCommandMsg


SENTRY, OUTPOST, BASE = 5, 6, 7  # Pinned tiny_resnet ArmorName order.
BEHAVIOR_COMMAND_NS = 300_000_000
SECONDARY_TARGETS = {
    "ENEMY_VEHICLE_FIRST": None,
    "ENEMY_VEHICLE_THEN_OUTPOST": OUTPOST,
    "ENEMY_VEHICLE_THEN_BASE": BASE,
}


@dataclass(frozen=True)
class ArmorObservation:
    x: float
    y: float
    bar_height_px: float
    label: int
    confidence: float


@dataclass(frozen=True)
class AimObservation:
    label: int
    yaw: float
    raw_pitch: float
    distance: float
    confidence: float


def marker_mask(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = np.uint8((gray >= 150) & (image.max(axis=2)-image.min(axis=2) <= 70))
    points = cv2.findNonZero(mask)
    if points is None:
        return None, 0, 0
    x, y, width, height = cv2.boundingRect(points)
    if min(width, height) < 6:
        return None, width, height
    normalized = cv2.resize(mask[y:y+height,x:x+width]*255, (32,44), interpolation=cv2.INTER_AREA) >= 110
    return normalized, width, height


def outpost_marker_score(frame, reference, x1, x2, center_y, height):
    spacing = abs(x2-x1)
    left = max(0, round(min(x1,x2)+.16*spacing))
    right = min(frame.shape[1], round(max(x1,x2)-.16*spacing))
    top = max(0, round(center_y-.92*height))
    bottom = min(frame.shape[0], round(center_y+.92*height))
    if left >= right or top >= bottom:
        return 0.
    shape, width, glyph_height = marker_mask(frame[top:bottom,left:right])
    if shape is None or not .35 <= width/spacing <= .60 or not 1.0 <= glyph_height/height <= 1.75:
        return 0.
    return float((shape & reference).sum() / (shape | reference).sum())


def detect_enemy_armors(bgr: np.ndarray, target_red: bool, classifier, outpost_reference=None) -> list[ArmorObservation]:
    """Return classified competition armor observations in a simulator RGB frame."""
    if bgr.size == 0:
        return []
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    if target_red:
        m1 = cv2.inRange(hsv, (0, 120, 140), (24, 255, 255))
        m2 = cv2.inRange(hsv, (165, 120, 140), (180, 255, 255))
        mask = cv2.bitwise_or(m1, m2)
    else:
        mask = cv2.inRange(hsv, (86, 110, 140), (135, 255, 255))

    # A narrow lightbar can have a nearly zero polygon area despite covering many pixels.
    _, _, components, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    bars: list[tuple[float, float, float, float]] = []
    for x, y, w, h, pixels in components[1:]:
        if pixels < 6 or h < 5:
            continue
        aspect = h / float(w)
        if aspect < 1.5 or aspect > 20.0:
            continue
        bars.append((x + w * 0.5, y + h * 0.5, float(w), float(h)))

    if len(bars) < 2:
        return []

    candidates = []
    for i in range(len(bars)):
        x1, y1, w1, h1 = bars[i]
        for j in range(i + 1, len(bars)):
            x2, y2, w2, h2 = bars[j]
            # Arena windows can form two squat blue components. Require a slender bar
            # on at least one side, allowing the opposite bar to bloom at an oblique view.
            if max(h1 / w1, h2 / w2) < 2.0:
                continue
            avg_h = 0.5 * (h1 + h2)
            if abs(h1 - h2) / max(1.0, avg_h) > 0.45:
                continue
            dx = abs(x1 - x2)
            dy = abs(y1 - y2)
            if dy > avg_h * 0.65:
                continue
            ratio = dx / max(1.0, avg_h)
            if ratio < 1.2 or ratio > 5.2:
                continue
            candidates.append((dx * avg_h, x1, y1, h1, x2, y2, h2))
    height, width = bgr.shape[:2]
    detections = []
    for _score, x1, y1, h1, x2, y2, h2 in sorted(candidates, reverse=True)[:12]:
        avg_h = 0.5 * (h1 + h2)
        cx_p, cy_p = 0.5 * (x1 + x2), 0.5 * (y1 + y2)
        if outpost_reference is not None:
            score = outpost_marker_score(bgr, outpost_reference, x1, x2, cy_p, avg_h)
            if score >= .61:
                detections.append(ArmorObservation(cx_p, cy_p, avg_h, OUTPOST, score))
                continue
        top = max(0, int(min(y1 - 1.125 * h1, y2 - 1.125 * h2)))
        bottom = min(height, int(max(y1 + 1.125 * h1, y2 + 1.125 * h2)))
        left, right = max(0, int(min(x1, x2))), min(width, int(max(x1, x2)))
        if bottom <= top or right <= left:
            continue
        crop = cv2.cvtColor(bgr[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
        scale = 32 / max(crop.shape)
        w, h = max(1, int(crop.shape[1] * scale)), max(1, int(crop.shape[0] * scale))
        padded = np.zeros((32, 32), dtype=np.uint8)
        padded[:h, :w] = cv2.resize(crop, (w, h))
        classifier.setInput(cv2.dnn.blobFromImage(padded, 1.0 / 255.0))
        logits = classifier.forward().flatten()
        scores = np.exp(logits - logits.max())
        label = int(scores.argmax())
        confidence = float(scores[label] / scores.sum())
        if label in (SENTRY, OUTPOST, BASE) and confidence >= .75:
            detections.append(ArmorObservation(cx_p, cy_p, avg_h, label, confidence))
    return detections


def attitude_at(feedback, stamp):
    before = next((sample for sample in reversed(feedback) if sample[0] <= stamp), None)
    after = next((sample for sample in feedback if sample[0] >= stamp), None)
    if before and after and stamp-before[0] <= 45_000_000 and after[0]-stamp <= 45_000_000:
        if before[0] == after[0]:
            return before[1:]
        t = (stamp-before[0]) / (after[0]-before[0])
        return (before[1] + t*math.remainder(after[1]-before[1],2*math.pi),
                before[2] + t*(after[2]-before[2]))
    # Wait for the frame's next physical feedback sample instead of aiming from a stale angle.
    return None


def match_observation(previous, candidates):
    """Associate adjacent views of the same simulated target across armor face changes."""
    angular_gate = max(.06, min(.24, .45/previous.distance))
    best = None
    best_error = angular_gate
    for candidate in candidates:
        if candidate.label != previous.label or not .55 <= candidate.distance/previous.distance <= 1.8:
            continue
        error = math.hypot(math.remainder(candidate.yaw-previous.yaw, 2*math.pi),
                           candidate.raw_pitch-previous.raw_pitch)
        if error < best_error:
            best = candidate
            best_error = error
    return best


def ballistic_pitch_compensation(dist_m: float, raw_pitch_rad: float, muzzle_speed_mps: float = 24.5, g: float = 9.81) -> float:
    """Compute gravity-drop elevation angle (rad) for 17mm projectile.
    Note: world_pitch uses ROS convention (negative = up, positive = down), so caller subtracts this angle.
    """
    d = max(0.5, min(18.0, float(dist_m)))
    v0 = max(10.0, float(muzzle_speed_mps))
    horiz = max(0.3, d * math.cos(raw_pitch_rad))
    # 2-step iterative flight-time solve for accurate parabolic drop
    drop_rad = 0.0
    for _ in range(2):
        eff_pitch = raw_pitch_rad - drop_rad
        vx = max(5.0, v0 * math.cos(eff_pitch))
        t = horiz / vx
        drop_m = 0.5 * g * t * t
        drop_rad = math.atan2(drop_m, horiz)
    return min(0.32, max(0.0, drop_rad))


def world_target(world_yaw, world_pitch, x_over_focal, y_over_focal):
    cy, sy = math.cos(world_yaw), math.sin(world_yaw)
    cp, sp = math.cos(world_pitch), math.sin(world_pitch)
    forward = cp - y_over_focal * sp
    x = forward * cy + x_over_focal * sy
    y = forward * sy - x_over_focal * cy
    z = -sp - y_over_focal * cp
    return math.atan2(y, x), math.atan2(-z, math.hypot(x, y))


def ns(stamp):
    return stamp.sec * 1_000_000_000 + stamp.nanosec


class VisionAutoAimNode(Node):
    def __init__(self) -> None:
        super().__init__("rmsim_vision_autoaim")
        self.robot_id = str(self.declare_parameter("robot_id", "aim_sentry").value)
        model = Path(str(self.declare_parameter("classifier_path", "").value))
        if not model.is_file():
            raise RuntimeError(f"Missing competition armor classifier: {model}")
        self.classifier = cv2.dnn.readNetFromONNX(np.frombuffer(model.read_bytes(), np.uint8))
        marker = Path(__file__).resolve().parents[4] / 'assets/ui/armor_stickers/outpost_sticker.png'
        marker_image = cv2.imdecode(np.frombuffer(marker.read_bytes(),np.uint8),cv2.IMREAD_COLOR) if marker.is_file() else None
        self.outpost_reference = marker_mask(marker_image)[0] if marker_image is not None else None

        self.is_red = True
        self.game_running = False
        self.robot_alive = False
        self.behavior_permits = False
        self.behavior_stamp = -1
        self.fire_permitted = None
        self.fire_permit_stamp = None
        self.sim_match = False
        self.identity = None
        self.feedback = deque(maxlen=32)
        self.intrinsics = None
        self.pending_image = None
        self.target_priority = "ENEMY_VEHICLE_FIRST"
        self.targets = {SENTRY}
        self.track = None
        self.track_stamp = 0
        self.track_id = 0
        self.track_hits = 0
        self.track_visible = False
        self.command_firing = False
        self.permitted = False
        self.frames = self.paired = self.tracked = self.trigger_frames = 0
        self.last_diagnostic = 0

        qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=5, reliability=ReliabilityPolicy.RELIABLE)
        self.create_subscription(String, "/rmsim/referee/state", self._on_referee, qos)
        self.create_subscription(CameraInfo, f"/{self.robot_id}/camera/camera_info", self._on_info, qos_profile_sensor_data)
        self.create_subscription(Image, f"/{self.robot_id}/camera/image_raw", self._on_image, qos_profile_sensor_data)
        self.create_subscription(String, f"/rmsim/{self.robot_id}/state", self._on_state, 5)
        self.create_subscription(Int32, f"/{self.robot_id}/behavior/auto_aim_switch", self._on_behavior, 5)
        self.create_subscription(Int32, f"/{self.robot_id}/behavior/fire_permit", self._on_fire_permit, 5)
        self.create_subscription(String, f"/{self.robot_id}/behavior/target_priority", self._on_priority, 5)

        self.track_pub = self.create_publisher(String, f"/{self.robot_id}/behavior/vision_track", 1)
        self.cmd_pub = self.create_publisher(AutoAimCommandMsg, f"/{self.robot_id}/autoaim_command", 1)
        self.permit_pub = self.create_publisher(Int32, f"/{self.robot_id}/auto_aim_switch", 1)
        self.create_timer(.05, self._watchdog)
        self.get_logger().info(f"Simulator vision ready (robot_id={self.robot_id}, classifier={model.name})")

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
        self.robot_alive = int(rs.get("current_hp", 0)) > 0
        enemy = "blue" if self.is_red else "red"
        field_devices = ref.get("field_devices") or {}
        enemy_site = field_devices.get(enemy) or {}
        enemy_hp = ref.get("all_robot_hp") or {}
        self.targets = {SENTRY} if enemy_hp.get(f"{enemy}_7_robot_hp", 1) > 0 else set()
        outposts = (field_devices.get(site) or {} for site in ("red", "blue"))
        if any(site.get("outpost_hp", 0) > 0 and site.get("outpost_owner_red") is (not self.is_red)
               for site in outposts):
            self.targets.add(OUTPOST)
        if enemy_site.get("base_hp", 0) > 0 and enemy_site.get("base_protected") is False:
            self.targets.add(BASE)
        if not self.robot_alive or not self.game_running:
            self._release()

    def _on_info(self, msg: CameraInfo) -> None:
        if len(msg.k) == 9 and msg.k[0] > 1.0 and msg.k[4] > 1.0:
            self.intrinsics = (msg.width, msg.height, msg.header.frame_id, msg.k[0], msg.k[4], msg.k[2], msg.k[5])

    def _on_behavior(self, msg: Int32) -> None:
        self.behavior_permits = msg.data == 1
        self.behavior_stamp = self.get_clock().now().nanoseconds
        if not self.behavior_permits:
            self._release()

    def _on_fire_permit(self, msg: Int32) -> None:
        self.fire_permitted = msg.data == 1
        self.fire_permit_stamp = self.get_clock().now().nanoseconds
        if not self.fire_permitted:
            self._stop_firing(self.fire_permit_stamp)

    def _on_priority(self, msg: String) -> None:
        if msg.data in SECONDARY_TARGETS:
            self.target_priority = msg.data
            if self.track is not None and self.track.label not in self._allowed_labels():
                self._release()

    def _on_state(self, msg: String) -> None:
        try:
            d = json.loads(msg.data)
        except ValueError:
            self._release()
            return
        if not isinstance(d, dict):
            self._release()
            return
        aim = d.get("aim") or {}
        identity = (d.get("session"), d.get("epoch"))
        if identity != self.identity:
            self.identity = identity
            self.fire_permit_stamp = None
            self.feedback.clear()
            self.pending_image = None
            self._release()
        self.sim_match = bool(d.get("state_fresh") and d.get("drive", {}).get("match_autonomy")
                              and aim.get("enabled") and "match_fire" in aim)
        if self.sim_match:
            self.feedback.append((int(aim["stamp_ns"]), aim["world_yaw_rad"], aim["world_pitch_rad"]))
            self._process_image()
        else:
            self._release()

    def _release(self):
        self.track = None
        self.track_hits = 0
        self.track_visible = False
        self.command_firing = False
        if self.permitted:
            self.permit_pub.publish(Int32(data=0))
            self.permitted = False

    def _watchdog(self):
        now = self.get_clock().now().nanoseconds
        if self.behavior_permits and not 0 <= now-self.behavior_stamp <= BEHAVIOR_COMMAND_NS:
            self.behavior_permits = False
            self._release()
        if self.track is not None and (not 0 <= now-self.track_stamp <= MATCH_TRACK_MEMORY_NS
                                      or self.track.label not in self._allowed_labels()):
            self._release()
        elif self.track is not None and now-self.track_stamp > MATCH_FIRE_OBSERVATION_NS:
            self.track_visible = False
        if self.command_firing and not self._can_fire(now):
            self._stop_firing(now)
        self.track_pub.publish(String(data=json.dumps({
            "stamp_ns": now,
            "session": self.identity[0] if self.identity else None,
            "epoch": self.identity[1] if self.identity else None,
            "track_id": self.track_id if self.track else None,
            "label": self.track.label if self.track else None,
            "observation_stamp_ns": self.track_stamp if self.track else None,
            "visible": self.track_visible,
            "firing": self.command_firing,
            "fire_permit": self._fire_gate_open(now) if self.fire_permitted is not None else None,
            "target_priority": self.target_priority,
        }, separators=(",", ":"))))
        if now - self.last_diagnostic >= 2_000_000_000:
            self.last_diagnostic = now
            self.get_logger().info(f"vision frames={self.frames} paired={self.paired} tracked={self.tracked} "
                                   f"trigger_frames={self.trigger_frames} permitted={self.permitted} "
                                   f"track_label={self.track.label if self.track else -1}")

    def _allowed_labels(self):
        return self.targets & {SENTRY, SECONDARY_TARGETS[self.target_priority]}

    def _fire_gate_open(self, now):
        return self.fire_permitted is None or (
            self.fire_permitted and self.fire_permit_stamp is not None
            and 0 <= now-self.fire_permit_stamp <= BEHAVIOR_COMMAND_NS
        )

    def _can_fire(self, now):
        # The referee enforces cadence, heat and ammo while the simulated S7 tracks a visible opponent.
        return (self.track_visible and self.track_hits >= 2 and self.game_running and self.robot_alive
                and self.behavior_permits and self._fire_gate_open(now) and self.track.label in self._allowed_labels()
                and 0 <= now-self.track_stamp <= MATCH_FIRE_OBSERVATION_NS)

    def _send_aim(self, target, stamp, shoot):
        pitch = target.raw_pitch - ballistic_pitch_compensation(target.distance, target.raw_pitch)
        cmd = AutoAimCommandMsg()
        cmd.timestamp.sec, cmd.timestamp.nanosec = divmod(stamp, 1_000_000_000)
        cmd.control = True
        cmd.shoot = bool(shoot)
        cmd.yaw = target.yaw
        cmd.pitch = pitch
        self.permit_pub.publish(Int32(data=1))
        self.permitted = True
        self.command_firing = cmd.shoot
        self.trigger_frames += int(cmd.shoot)
        self.cmd_pub.publish(cmd)

    def _stop_firing(self, now):
        if self.command_firing and 0 <= now-self.track_stamp <= MATCH_FIRE_OBSERVATION_NS:
            self._send_aim(self.track, self.track_stamp, False)
        self.command_firing = False

    def _miss_observation(self, now):
        self.track_visible = False
        self._stop_firing(now)

    def _on_image(self, msg: Image) -> None:
        self.frames += 1
        self.pending_image = msg
        self._process_image()

    def _process_image(self) -> None:
        msg = self.pending_image
        if not (msg and self.sim_match and self.game_running and self.robot_alive
                and self.behavior_permits and self.intrinsics and self.feedback):
            return
        stamp = ns(msg.header.stamp)
        now = self.get_clock().now().nanoseconds
        if now < stamp:
            return
        if now-stamp > 165_000_000:
            self.pending_image = None
            return
        sample = attitude_at(self.feedback, stamp)
        if sample is None:
            return
        self.paired += 1
        self.pending_image = None
        if self.track is not None and stamp <= self.track_stamp:
            return
        width, height, frame, fx, fy, cx, cy = self.intrinsics
        if (msg.width, msg.height, msg.header.frame_id) != (width, height, frame):
            self._miss_observation(now)
            return
        arr = np.frombuffer(msg.data, dtype=np.uint8)
        channels = 3 if msg.encoding in ("rgb8", "bgr8") else (4 if msg.encoding in ("rgba8", "bgra8") else 0)
        if channels == 0 or msg.step < width * channels or arr.size < height * msg.step:
            self._miss_observation(now)
            return
        img = arr[:height * msg.step].reshape((height, msg.step))[:, :width * channels].reshape((height, width, channels))
        bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if msg.encoding.startswith("rgb") else img[:, :, :3]

        allowed = self._allowed_labels()
        candidates = []
        marker = self.outpost_reference if OUTPOST in allowed else None
        for det in detect_enemy_armors(bgr, target_red=not self.is_red, classifier=self.classifier, outpost_reference=marker):
            if det.label in allowed:
                yaw, raw_pitch = world_target(*sample, (det.x-cx)/fx, (det.y-cy)/fy)
                distance = max(.6, min(16., .055*fy/det.bar_height_px))
                candidates.append(AimObservation(det.label, yaw, raw_pitch, distance, det.confidence))
        vehicle_visible = any(candidate.label == SENTRY for candidate in candidates)
        if self.track is not None and (stamp-self.track_stamp > MATCH_TRACK_MEMORY_NS or self.track.label not in allowed
                                      or (self.track.label != SENTRY and vehicle_visible)):
            self._release()
        if self.track is None:
            if not candidates:
                return
            observation = max(candidates, key=lambda candidate: (candidate.label == SENTRY, candidate.confidence))
            self.track_id += 1
            self.track_hits = 1
        else:
            observation = match_observation(self.track, candidates)
            if observation is None:
                self._miss_observation(now)
                return
            self.track_hits = min(2, self.track_hits+1)
        self.track = observation
        self.track_stamp = stamp
        self.track_visible = True
        self.tracked += 1
        self._send_aim(observation, stamp, self._can_fire(now))


def main(args=None) -> None:
    rclpy.init(args=args)
    node = VisionAutoAimNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
