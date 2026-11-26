"""Publish authoritative RMUC referee state from UE5 to ROS 2."""
import json
import math
import time

from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from .field_joints import FieldDescriptionPublisher, base_joint_positions
from .control_dlls import load_package_dlls

try:
    from .dji_referee import DjiRefereePublisher
except ModuleNotFoundError as exc:
    if exc.name not in {'dji_referee_protocol', 'dji_referee_protocol.msg'}:
        raise
    DjiRefereePublisher = None

try:
    from pb_rm_interfaces.msg import (
        Buff,
        EventData,
        GameRobotHP,
        GameStatus,
        RfidStatus,
        RobotStatus,
    )
    HAS_PB_RM_INTERFACES = True
except ImportError:
    HAS_PB_RM_INTERFACES = False


class RefereePublisher:
    def __init__(self, node):
        self.node = node
        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        )
        self.json_pub = node.create_publisher(String, '/rmsim/referee/state', qos)
        self.field_description = None
        self.field_qos = qos
        self.last_1hz = 0.0
        self.last_3hz = 0.0
        self.last_10hz = 0.0
        self.last_progress = None
        self.last_hp_sig = None
        self.dji = DjiRefereePublisher(node, qos) if DjiRefereePublisher else None

        if HAS_PB_RM_INTERFACES:
            load_package_dlls('pb_rm_interfaces')
            self.game_status_pub = node.create_publisher(GameStatus, '/referee/game_status', qos)
            self.all_hp_pub = node.create_publisher(GameRobotHP, '/referee/all_robot_hp', qos)
            self.robot_status_pub = node.create_publisher(RobotStatus, '/referee/robot_status', qos)
            self.event_data_pub = node.create_publisher(EventData, '/referee/event_data', qos)
            self.buff_pub = node.create_publisher(Buff, '/referee/buff', qos)
            self.rfid_pub = node.create_publisher(RfidStatus, '/referee/rfid_status', qos)
            node.get_logger().info('RMUC referee bridge enabled (pb_rm_interfaces V1.7.0)')
        else:
            self.game_status_pub = None
            node.get_logger().warning('pb_rm_interfaces not found; legacy typed referee topics are unavailable')

    def observe(self, state):
        ref = state.get('referee')
        if not isinstance(ref, dict) or not ref.get('available'):
            return
        positions = base_joint_positions(ref)
        if positions is not None:
            if self.field_description is None:
                self.field_description = FieldDescriptionPublisher(self.node, self.field_qos)
            self.field_description.observe(state['sim_time_ns'], positions)
        now = time.monotonic()
        if self.dji is not None:
            self.dji.observe(state, ref, now)
        gs = ref.get('game_status') or {}
        hp = ref.get('all_robot_hp') or {}
        rs = ref.get('robot_status') or {}
        ev = ref.get('event_data') or {}
        bf = ref.get('buff') or {}
        rf = ref.get('rfid_status') or {}

        progress = int(gs.get('game_progress', 0))
        hp_sig = (
            int(hp.get('red_7_robot_hp', 0)),
            int(hp.get('blue_7_robot_hp', 0)),
            int(hp.get('red_outpost_hp', 0)),
            int(hp.get('blue_outpost_hp', 0)),
            int(hp.get('red_base_hp', 0)),
            int(hp.get('blue_base_hp', 0)),
        )
        progress_changed = (progress != self.last_progress)
        hp_changed = (hp_sig != self.last_hp_sig)
        hp_deduced = bool(rs.get('is_hp_deduced', False))

        if now - self.last_3hz >= 0.33 or progress_changed or hp_changed:
            self.json_pub.publish(String(data=json.dumps(ref, separators=(',', ':'))))

        if not HAS_PB_RM_INTERFACES:
            if now - self.last_3hz >= 0.33:
                self.last_3hz = now
            self.last_progress = progress
            self.last_hp_sig = hp_sig
            return

        # 1 Hz (or immediate on stage change): GameStatus & EventData
        if now - self.last_1hz >= 1.0 or progress_changed:
            self.last_1hz = now
            msg_gs = GameStatus()
            msg_gs.game_progress = progress
            msg_gs.stage_remain_time = int(gs.get('stage_remain_time', 0))
            self.game_status_pub.publish(msg_gs)

            msg_ev = EventData()
            msg_ev.non_overlapping_supply_zone = int(ev.get('non_overlapping_supply_zone', 0))
            msg_ev.overlapping_supply_zone = int(ev.get('overlapping_supply_zone', 0))
            msg_ev.supply_zone = int(ev.get('supply_zone', 0))
            msg_ev.small_energy = int(ev.get('small_energy', 0))
            msg_ev.big_energy = int(ev.get('big_energy', 0))
            msg_ev.central_highland = int(ev.get('central_highland', 0))
            msg_ev.trapezoidal_highland = int(ev.get('trapezoidal_highland', 0))
            msg_ev.center_gain_zone = int(ev.get('center_gain_zone', 0))
            self.event_data_pub.publish(msg_ev)

        # 3 Hz (or immediate on HP change): GameRobotHP, Buff, RfidStatus
        if now - self.last_3hz >= 0.33 or hp_changed:
            self.last_3hz = now
            msg_hp = GameRobotHP()
            msg_hp.red_1_robot_hp = int(hp.get('red_1_robot_hp', 0))
            msg_hp.red_2_robot_hp = int(hp.get('red_2_robot_hp', 0))
            msg_hp.red_3_robot_hp = int(hp.get('red_3_robot_hp', 0))
            msg_hp.red_4_robot_hp = int(hp.get('red_4_robot_hp', 0))
            msg_hp.red_7_robot_hp = int(hp.get('red_7_robot_hp', 0))
            msg_hp.red_outpost_hp = int(hp.get('red_outpost_hp', 0))
            msg_hp.red_base_hp = int(hp.get('red_base_hp', 0))
            msg_hp.blue_1_robot_hp = int(hp.get('blue_1_robot_hp', 0))
            msg_hp.blue_2_robot_hp = int(hp.get('blue_2_robot_hp', 0))
            msg_hp.blue_3_robot_hp = int(hp.get('blue_3_robot_hp', 0))
            msg_hp.blue_4_robot_hp = int(hp.get('blue_4_robot_hp', 0))
            msg_hp.blue_7_robot_hp = int(hp.get('blue_7_robot_hp', 0))
            msg_hp.blue_outpost_hp = int(hp.get('blue_outpost_hp', 0))
            msg_hp.blue_base_hp = int(hp.get('blue_base_hp', 0))
            self.all_hp_pub.publish(msg_hp)

            msg_bf = Buff()
            msg_bf.recovery_buff = int(bf.get('recovery_buff', 0))
            msg_bf.cooling_buff = int(bf.get('cooling_buff', 1))
            msg_bf.defence_buff = int(bf.get('defence_buff', 0))
            msg_bf.vulnerability_buff = int(bf.get('vulnerability_buff', 0))
            msg_bf.attack_buff = int(bf.get('attack_buff', 0))
            msg_bf.remaining_energy = int(bf.get('remaining_energy', 0x32))
            self.buff_pub.publish(msg_bf)

            msg_rf = RfidStatus()
            msg_rf.base_gain_point = bool(rf.get('base_gain_point', False))
            msg_rf.friendly_central_highland_upper_gain_point = bool(rf.get('friendly_central_highland_upper_gain_point', False))
            msg_rf.friendly_fortress_gain_point = bool(rf.get('friendly_fortress_gain_point', False))
            msg_rf.friendly_outpost_gain_point = bool(rf.get('friendly_outpost_gain_point', False))
            msg_rf.friendly_supply_zone_non_exchange = bool(rf.get('friendly_supply_zone_non_exchange', False))
            msg_rf.friendly_supply_zone_exchange = bool(rf.get('friendly_supply_zone_exchange', False))
            msg_rf.center_gain_point = bool(rf.get('center_gain_point', False))
            self.rfid_pub.publish(msg_rf)

        # 10 Hz (or immediate on hit): RobotStatus
        if now - self.last_10hz >= 0.10 or hp_deduced:
            self.last_10hz = now
            msg_rs = RobotStatus()
            msg_rs.robot_id = int(rs.get('robot_id', 7))
            msg_rs.robot_level = int(rs.get('robot_level', 1))
            msg_rs.current_hp = int(rs.get('current_hp', 400))
            msg_rs.maximum_hp = int(rs.get('maximum_hp', 400))
            msg_rs.shooter_barrel_cooling_value = int(rs.get('shooter_barrel_cooling_value', 80))
            msg_rs.shooter_barrel_heat_limit = int(rs.get('shooter_barrel_heat_limit', 400))
            msg_rs.shooter_17mm_1_barrel_heat = int(rs.get('shooter_17mm_1_barrel_heat', 0))
            msg_rs.robot_pos.position.x = float(rs.get('pos_x', 0.0))
            msg_rs.robot_pos.position.y = float(rs.get('pos_y', 0.0))
            msg_rs.robot_pos.position.z = float(rs.get('pos_z', 0.0))
            half_yaw = 0.5 * float(rs.get('yaw_rad', 0.0))
            msg_rs.robot_pos.orientation.z = math.sin(half_yaw)
            msg_rs.robot_pos.orientation.w = math.cos(half_yaw)
            msg_rs.armor_id = int(rs.get('armor_id', 0))
            msg_rs.hp_deduction_reason = int(rs.get('hp_deduction_reason', 0))
            msg_rs.projectile_allowance_17mm = int(rs.get('projectile_allowance_17mm', 750))
            msg_rs.remaining_gold_coin = int(rs.get('remaining_gold_coin', 200))
            msg_rs.is_hp_deduced = hp_deduced
            self.robot_status_pub.publish(msg_rs)

        self.last_progress = progress
        self.last_hp_sig = hp_sig
