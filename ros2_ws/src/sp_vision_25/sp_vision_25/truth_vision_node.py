"""Zero-camera ground-truth auto-aim node for RMUC 2027 autonomous sentries.

Replaces pixel-based camera rendering + YOLOv8 inference with referee-supplied
3D ground-truth targets + line-of-sight visibility checks, publishing the exact
same ROS 2 topics (`/{robot_id}/autoaim_command`, `/{robot_id}/auto_aim_switch`,
`/{robot_id}/behavior/vision_track`) as `sp_vision_25/vision_node`.
"""
from __future__ import annotations

import json
import math
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from std_msgs.msg import Int32, String
from sp_msgs.msg import AutoAimCommandMsg


def _wrap_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


class TruthVisionNode(Node):
    def __init__(self) -> None:
        super().__init__("truth_vision_node")
        self.robot_id = str(self.declare_parameter("robot_id", "aim_sentry").value).strip() or "aim_sentry"
        self.muzzle_v = float(self.declare_parameter("muzzle_speed_mps", 24.5).value)
        self.require_bt = bool(self.declare_parameter("require_behavior_switch", True).value)
        self.max_range_m = float(self.declare_parameter("max_range_m", 12.0).value)
        self.shoot_tol_rad = math.radians(float(self.declare_parameter("shoot_tol_deg", 5.0).value))
        self.struct_shoot_tol_rad = math.radians(float(self.declare_parameter("structure_shoot_tol_deg", 7.5).value))

        self.game_running = not self.require_bt
        self.robot_alive = True
        self.is_red = True
        self.self_pos: tuple[float, float, float] | None = None
        self.cur_yaw = 0.0
        self.cur_pitch = 0.0
        self.sim_time_ns = 0
        self.session = None
        self.epoch = None
        self.truth_targets: list[dict] = []

        self.bt_seen = False
        self.bt_permits = not self.require_bt
        self.bt_stamp_ns = 0
        self.fire_permit: bool | None = None
        self.fire_permit_stamp_ns = 0
        self.target_priority = "ENEMY_VEHICLE_FIRST"

        self.track_id = 0
        self.track_name: str | None = None
        self.track_label: int | None = None
        self.track_visible = False
        self.command_firing = False
        self.last_dist_m: float | None = None
        self.last_enemy_xy: tuple[float, float] | None = None
        self.last_enemy_vel = (0.0, 0.0)
        self.last_cmd_stamp_ns = 0

        reliable = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )
        self.create_subscription(String, "/rmsim/referee/state", self._on_referee, reliable)
        self.create_subscription(String, f"/rmsim/{self.robot_id}/state", self._on_sim_state, 5)
        self.create_subscription(Int32, f"/{self.robot_id}/behavior/auto_aim_switch", self._on_bt_switch, 5)
        self.create_subscription(Int32, f"/{self.robot_id}/behavior/fire_permit", self._on_fire_permit, 5)
        self.create_subscription(String, f"/{self.robot_id}/behavior/target_priority", self._on_priority, 5)

        self.cmd_pub = self.create_publisher(AutoAimCommandMsg, f"/{self.robot_id}/autoaim_command", 1)
        self.permit_pub = self.create_publisher(Int32, f"/{self.robot_id}/auto_aim_switch", 1)
        self.track_pub = self.create_publisher(String, f"/{self.robot_id}/behavior/vision_track", 1)

        self.create_timer(0.02, self._tick)  # 50 Hz
        self.get_logger().info(
            f"truth_vision_node ready (robot_id={self.robot_id}, zero-camera mode, v0={self.muzzle_v:.1f} m/s)"
        )

    def _now_ns(self) -> int:
        if self.sim_time_ns > 0:
            return self.sim_time_ns
        return self.get_clock().now().nanoseconds or int(time.time() * 1e9)

    def _on_sim_state(self, msg: String) -> None:
        try:
            data = json.loads(msg.data)
        except Exception:
            return
        if not isinstance(data, dict):
            return
        st = data.get("sim_time_ns")
        if isinstance(st, (int, float)) and st > 0:
            self.sim_time_ns = int(st)
        if "session" in data:
            self.session = data["session"]
        if "epoch" in data:
            self.epoch = data["epoch"]
        aim = data.get("aim")
        if isinstance(aim, dict):
            wy = aim.get("world_yaw_rad")
            wp = aim.get("world_pitch_rad")
            if isinstance(wy, (int, float)):
                self.cur_yaw = float(wy)
            if isinstance(wp, (int, float)):
                self.cur_pitch = float(wp)
        pose = data.get("pose")
        if isinstance(pose, dict):
            pm = pose.get("position_m")
            if isinstance(pm, list) and len(pm) >= 3:
                self.self_pos = (float(pm[0]), float(pm[1]), float(pm[2]))

    def _on_referee(self, msg: String) -> None:
        try:
            ref = json.loads(msg.data)
        except Exception:
            return
        if not isinstance(ref, dict):
            return
        gs = ref.get("game_status")
        if isinstance(gs, dict) and isinstance(gs.get("game_progress"), (int, float)):
            self.game_running = int(gs["game_progress"]) == 4
        rs = ref.get("robot_status")
        if isinstance(rs, dict):
            if isinstance(rs.get("robot_id"), (int, float)):
                self.is_red = int(rs["robot_id"]) < 100
            if isinstance(rs.get("current_hp"), (int, float)):
                self.robot_alive = int(rs["current_hp"]) > 0
            if isinstance(rs.get("pos_x"), (int, float)) and isinstance(rs.get("pos_y"), (int, float)):
                pz = float(rs.get("pos_z", 0.35))
                if self.self_pos is None:
                    self.self_pos = (float(rs["pos_x"]), float(rs["pos_y"]), pz)
        tt = ref.get("truth_targets")
        if isinstance(tt, list):
            self.truth_targets = [t for t in tt if isinstance(t, dict)]

    def _on_bt_switch(self, msg: Int32) -> None:
        self.bt_seen = True
        self.bt_permits = int(msg.data) > 0
        self.bt_stamp_ns = self._now_ns()

    def _on_fire_permit(self, msg: Int32) -> None:
        self.fire_permit = int(msg.data) > 0
        self.fire_permit_stamp_ns = self._now_ns()

    def _on_priority(self, msg: String) -> None:
        val = msg.data.strip().upper()
        if val:
            self.target_priority = val

    def _bt_active(self, now_ns: int) -> bool:
        if not self.require_bt:
            return True
        return self.bt_seen and self.bt_permits and (now_ns - self.bt_stamp_ns) <= 2_000_000_000

    def _fire_gate_open(self, now_ns: int) -> bool:
        if self.fire_permit is None:
            return True
        if (now_ns - self.fire_permit_stamp_ns) > 2_000_000_000:
            return True
        return bool(self.fire_permit)

    def _pick_target(self) -> dict | None:
        if not self.truth_targets:
            return None
        prio = self.target_priority
        best = None
        best_score = None
        for t in self.truth_targets:
            if not t.get("visible", False):
                continue
            dist = float(t.get("dist", 999.0))
            if dist <= 0.2 or dist > self.max_range_m:
                continue
            name = str(t.get("name", ""))
            is_outpost = name == "outpost"
            is_base = name == "base"
            is_veh = not is_outpost and not is_base
            if prio == "OUTPOST_ONLY" and not is_outpost:
                continue
            if prio == "BASE_ONLY" and not is_base:
                continue
            if prio == "ENEMY_VEHICLE_ONLY" and not is_veh:
                continue

            if prio == "OUTPOST_FIRST":
                tier = 2 if is_outpost else (1 if is_veh else 0)
            elif prio == "ENEMY_VEHICLE_THEN_OUTPOST":
                tier = 2 if (is_veh and dist <= 7.5) else (1 if is_outpost else (1 if is_veh else 0))
            else:
                tier = 2 if is_veh else (1 if is_outpost else 0)

            score = (tier, -dist)
            if best_score is None or score > best_score:
                best_score = score
                best = t
        return best

    def _solve_ballistic(self, sx: float, sy: float, sz: float, tgt: dict) -> tuple[float, float, float]:
        tx = float(tgt["x"])
        ty = float(tgt["y"])
        tz = float(tgt["z"])
        vx = float(tgt.get("vx", 0.0))
        vy = float(tgt.get("vy", 0.0))
        vz = float(tgt.get("vz", 0.0))
        v0 = max(10.0, self.muzzle_v)
        # 2-step iterative time-of-flight lead + gravity drop compensation
        tof = math.sqrt((tx - sx) ** 2 + (ty - sy) ** 2 + (tz - sz) ** 2) / v0
        for _ in range(2):
            px = tx + vx * tof
            py = ty + vy * tof
            pz = tz + vz * tof
            dx, dy, dz = px - sx, py - sy, pz - sz
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            tof = dist / v0
        dz_comp = dz + 0.5 * 9.81 * tof * tof
        horiz = max(1e-3, math.hypot(dx, dy))
        target_yaw = math.atan2(dy, dx)
        # World pitch in RMSim AimController is down-positive (-atan2(dz, horiz))
        target_pitch = -math.atan2(dz_comp, horiz)
        return target_yaw, target_pitch, dist

    def _tick(self) -> None:
        now_ns = self._now_ns()
        active = self.game_running and self.robot_alive and self._bt_active(now_ns)
        tgt = self._pick_target() if active else None

        if tgt is not None and self.self_pos is not None:
            sx, sy, sz = self.self_pos[0], self.self_pos[1], self.self_pos[2] + 0.38
            tyaw, tpitch, dist = self._solve_ballistic(sx, sy, sz, tgt)
            name = str(tgt.get("name", "vehicle"))
            label = int(tgt.get("label", 7))
            if self.track_name != name:
                self.track_id += 1
            self.track_name = name
            self.track_label = label
            self.track_visible = True
            self.last_dist_m = dist
            is_struct = name in ("outpost", "base")
            if not is_struct:
                self.last_enemy_xy = (float(tgt["x"]), float(tgt["y"]))
                self.last_enemy_vel = (float(tgt.get("vx", 0.0)), float(tgt.get("vy", 0.0)))
            else:
                self.last_enemy_xy = None
                self.last_enemy_vel = (0.0, 0.0)

            err_yaw = abs(_wrap_angle(tyaw - self.cur_yaw))
            err_pitch = abs(tpitch - self.cur_pitch)
            tol = self.struct_shoot_tol_rad if is_struct else self.shoot_tol_rad
            can_shoot = (err_yaw <= tol and err_pitch <= tol and self._fire_gate_open(now_ns))

            safe_stamp = max(now_ns, self.last_cmd_stamp_ns)
            self.last_cmd_stamp_ns = safe_stamp
            cmd = AutoAimCommandMsg()
            cmd.timestamp.sec = int(safe_stamp // 1_000_000_000)
            cmd.timestamp.nanosec = int(safe_stamp % 1_000_000_000)
            cmd.control = True
            cmd.shoot = bool(can_shoot)
            cmd.yaw = float(tyaw)
            cmd.pitch = float(tpitch)
            self.cmd_pub.publish(cmd)

            pmsg = Int32()
            pmsg.data = 1
            self.permit_pub.publish(pmsg)
            self.command_firing = bool(can_shoot)
        else:
            if self.command_firing or self.track_visible:
                safe_stamp = max(now_ns, self.last_cmd_stamp_ns)
                self.last_cmd_stamp_ns = safe_stamp
                cmd = AutoAimCommandMsg()
                cmd.timestamp.sec = int(safe_stamp // 1_000_000_000)
                cmd.timestamp.nanosec = int(safe_stamp % 1_000_000_000)
                cmd.control = False
                cmd.shoot = False
                cmd.yaw = float(self.cur_yaw)
                cmd.pitch = float(self.cur_pitch)
                self.cmd_pub.publish(cmd)
            self.track_visible = False
            self.command_firing = False

        track_json = {
            "stamp_ns": now_ns,
            "session": self.session,
            "epoch": self.epoch,
            "track_id": self.track_id if self.track_name else None,
            "label": self.track_label if self.track_name else None,
            "observation_stamp_ns": now_ns if self.track_visible else None,
            "visible": self.track_visible,
            "firing": self.command_firing,
            "fire_permit": self._fire_gate_open(now_ns) if self.fire_permit is not None else None,
            "target_priority": self.target_priority,
            "distance_m": round(self.last_dist_m, 2) if self.last_dist_m is not None else None,
            "self_x": round(self.self_pos[0], 2) if self.self_pos is not None else None,
            "self_y": round(self.self_pos[1], 2) if self.self_pos is not None else None,
            "enemy_x": round(self.last_enemy_xy[0], 2) if self.last_enemy_xy is not None else None,
            "enemy_y": round(self.last_enemy_xy[1], 2) if self.last_enemy_xy is not None else None,
            "enemy_vx": round(self.last_enemy_vel[0], 2) if self.last_enemy_xy is not None else 0.0,
            "enemy_vy": round(self.last_enemy_vel[1], 2) if self.last_enemy_xy is not None else 0.0,
        }
        tmsg = String()
        tmsg.data = json.dumps(track_json, separators=(",", ":"))
        self.track_pub.publish(tmsg)


def main() -> None:
    rclpy.init()
    node = TruthVisionNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
