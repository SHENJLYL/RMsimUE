"""Publish simulated referee data under the native DJI message contract."""
import math

from dji_referee_protocol.msg import (
    AllowedShoot,
    Constants,
    Constraints,
    DamageState,
    FieldEvent,
    GameStatus,
    GroundRobotPosition,
    RFIDStatus,
    RobotBuff,
    RobotHeat,
    RobotHP,
    RobotPerformance,
    RobotPosition,
    SelfColor,
    SentryDecisionSync,
)

from .control_dlls import load_package_dlls


RFID_BITS = (
    ('base_gain_point', 0),
    ('friendly_central_highland_upper_gain_point', 10),
    ('friendly_fortress_gain_point', 17),
    ('friendly_outpost_gain_point', 18),
    ('friendly_supply_zone_non_exchange', 19),
    ('friendly_supply_zone_exchange', 20),
    ('center_gain_point', 23),
)

# Defaults from RMUCGameReferee.h and RMUCTacticalRules.h (RMUC 2027 three-stance power limits).
STANCE_POWER_LIMIT_W = {'MOVE': 150, 'ATTACK': 100, 'DEFENSE': 100}
STANCE_POSTURE_CODE = {
    'ATTACK': Constants.SENTRY_POSTURE_ATTACK,
    'DEFENSE': Constants.SENTRY_POSTURE_DEFENSE,
    'MOVE': Constants.SENTRY_POSTURE_MOVE,
}


class DjiRefereePublisher:
    def __init__(self, node, qos):
        load_package_dlls('dji_referee_protocol')
        self.game = node.create_publisher(GameStatus, '/referee/common/game_status', qos)
        self.hp = node.create_publisher(RobotHP, '/referee/common/robot_hp', qos)
        self.performance = node.create_publisher(RobotPerformance, '/referee/common/robot_performance', qos)
        self.heat = node.create_publisher(RobotHeat, '/referee/common/robot_heat', qos)
        self.position = node.create_publisher(RobotPosition, '/referee/common/robot_position', qos)
        self.ammo = node.create_publisher(AllowedShoot, '/referee/common/allowed_shoot', qos)
        self.rfid = node.create_publisher(RFIDStatus, '/referee/common/rfid_status', qos)
        self.field = node.create_publisher(FieldEvent, '/referee/common/field_event', qos)
        self.buff = node.create_publisher(RobotBuff, '/referee/common/robot_buff', qos)
        self.ground = node.create_publisher(GroundRobotPosition, '/referee/common/ground_robot_position', qos)
        self.constraints = node.create_publisher(Constraints, '/referee/parsed/common/constraints', qos)
        self.self_color = node.create_publisher(SelfColor, '/referee/parsed/common/self_color', qos)
        self.damage = node.create_publisher(DamageState, '/referee/common/damage_state', qos)
        self.sentry_sync = node.create_publisher(SentryDecisionSync, '/referee/common/sentry_decision_sync', qos)
        self.last_stage = self.last_hp = self.last_self_hp = self.last_damage_tick = self.last_stance = None
        self.next_1hz = self.next_3hz = self.next_10hz = 0.0
        node.get_logger().info('RMUC referee bridge enabled (original dji_referee_protocol)')

    def observe(self, state, referee, now):
        game = referee.get('game_status') or {}
        robot = referee.get('robot_status') or {}
        hp = referee.get('all_robot_hp') or {}
        rfid = referee.get('rfid_status') or {}
        progress = int(game.get('game_progress', 0))
        stream = state['session'], state['epoch']
        stage = stream, progress
        stage_changed = stage != self.last_stage
        hp_changed = hp != self.last_hp
        stance_changed = referee.get('sentry_stance') != self.last_stance
        self_hp = robot.get('current_hp')

        if stage_changed or stance_changed or now >= self.next_1hz:
            self.next_1hz = now + 1.0
            self._publish_field(referee, game, robot, rfid, progress)

        if stage_changed or hp_changed or now >= self.next_3hz:
            self.next_3hz = now + 1.0 / 3.0
            self.hp.publish(RobotHP(**{
                name: int(hp.get(name, 0)) for name in RobotHP.get_fields_and_field_types()
            }))
            buff = referee.get('buff') or {}
            self.buff.publish(RobotBuff(**{
                name: int(buff.get(name, 0)) for name in RobotBuff.get_fields_and_field_types()
            }))
            bits = sum(1 << bit for name, bit in RFID_BITS if rfid.get(name))
            self.rfid.publish(RFIDStatus(detected_rfid_bits_low=bits))

        if stage_changed or hp_changed or now >= self.next_10hz:
            self.next_10hz = now + 0.1
            self._publish_robot(referee, robot, progress)

        damage_tick = stream, state['tick']
        # The UE hit flag can clear between network snapshots. The referee retains the
        # latest armor and damage reason, so a drop within the same running stage can recover it.
        hp_dropped = (
            not stage_changed and progress == Constants.GAME_STAGE_IN_GAME
            and self_hp is not None and self.last_self_hp is not None and self_hp < self.last_self_hp
        )
        damage_reported = robot.get('is_hp_deduced') or hp_dropped
        has_damage_details = 'armor_id' in robot and 'hp_deduction_reason' in robot
        if damage_reported and damage_tick != self.last_damage_tick and has_damage_details:
            self.last_damage_tick = damage_tick
            self.damage.publish(DamageState(
                armor_id=int(robot['armor_id']), damage_type=int(robot['hp_deduction_reason']),
            ))

        self.last_stage = stage
        self.last_self_hp = self_hp
        self.last_stance = referee.get('sentry_stance')
        if hp_changed:
            self.last_hp = dict(hp)

    def _publish_field(self, referee, game, robot, rfid, progress):
        self.game.publish(GameStatus(
            game_type=Constants.GAME_TYPE_ROBO_MASTER,
            game_progress=progress,
            stage_remain_time=int(game.get('stage_remain_time', 0)),
            sync_timestamp=int(game.get('sync_timestamp', 0)),
        ))
        event = referee.get('event_data') or {}
        self.field.publish(FieldEvent(
            supply_area_1=bool(event.get('supply_zone')),
            small_energy_mech=Constants.ENERGY_MECH_ACTIVATED if event.get('small_energy') else 0,
            big_energy_mech=Constants.ENERGY_MECH_ACTIVATED if event.get('big_energy') else 0,
            central_highland=int(event.get('central_highland', 0)),
            trapezoid_highland=bool(event.get('trapezoidal_highland')),
            center_buff_point=int(event.get('center_gain_zone', 0)),
            fortress_buff_point=int(bool(rfid.get('friendly_fortress_gain_point'))),
            outpost_buff_point=int(bool(rfid.get('friendly_outpost_gain_point'))),
            base_buff_point=bool(rfid.get('base_gain_point')),
        ))
        if all(name in robot for name in ('pos_x', 'pos_y', 'yaw_rad')):
            # On RMUC2027, +Y is arena north. DJI uses a compass heading in degrees.
            self.position.publish(RobotPosition(
                x=float(robot['pos_x']), y=float(robot['pos_y']),
                angle=(90.0 - math.degrees(float(robot['yaw_rad']))) % 360.0,
            ))
        # The sentry-only match carries no position measurements for the other roles.
        ground = referee.get('ground_robot_position') or {}
        ground_fields = GroundRobotPosition.get_fields_and_field_types()
        if all(name in ground for name in ground_fields):
            self.ground.publish(GroundRobotPosition(**{name: float(ground[name]) for name in ground_fields}))
        robot_id = int(robot.get('robot_id', 0))
        own_key = 'red' if (1 <= robot_id < 100) else 'blue'
        own_site = (referee.get('field_devices') or {}).get(own_key) or {}
        hp = int(robot.get('current_hp', 0))
        gold = int(robot.get('remaining_gold_coin', 0))
        posture = STANCE_POSTURE_CODE.get(referee.get('sentry_stance'), Constants.SENTRY_POSTURE_MOVE)
        can_exchange = bool(own_site.get('local_ammo_purchase_available') or own_site.get('remote_ammo_purchase_available'))
        self.sentry_sync.publish(SentryDecisionSync(
            exchanged_ammo=0,
            remote_ammo_count=0,
            remote_hp_count=0,
            can_free_revive=(hp <= 0),
            can_buy_revive=(hp <= 0 and gold >= 50),
            revive_cost=50,
            in_disengage=bool(own_site.get('remote_ammo_purchase_available', False)),
            remaining_ammo_exchange=1000 if can_exchange else 0,
            sentry_posture=posture,
            can_activate_rune=False,
        ))

    def _publish_robot(self, referee, robot, progress):
        robot_id = int(robot.get('robot_id', 0))
        hp = int(robot.get('current_hp', 0))
        heat = int(robot.get('shooter_17mm_1_barrel_heat', 0))
        heat_limit = int(robot.get('shooter_barrel_heat_limit', 0))
        chassis_power = float(referee.get('chassis_power_w', 0.0))
        default_power_limit = STANCE_POWER_LIMIT_W.get(referee.get('sentry_stance'), 100)
        power_limit = int(referee.get('chassis_power_limit_w', default_power_limit))
        shooter_enabled = hp > 0 and not referee.get('shooter_locked', True)
        ammo = int(robot.get('projectile_allowance_17mm', 0))
        fortress_reserve = int(robot.get('fortress_reserve_17mm', 0))
        self.performance.publish(RobotPerformance(
            robot_id=robot_id,
            robot_level=int(robot.get('robot_level', 0)),
            current_hp=hp,
            maximum_hp=int(robot.get('maximum_hp', 0)),
            shooter_barrel_cooling_value=int(robot.get('shooter_barrel_cooling_value', 0)),
            shooter_barrel_heat_limit=heat_limit,
            chassis_power_limit=power_limit,
            power_management_chassis_output=bool(referee.get('chassis_powered')),
            power_management_shooter_output=shooter_enabled,
        ))
        self.heat.publish(RobotHeat(
            chassis_current_power=chassis_power,
            buffer_energy=round(float(referee.get('chassis_buffer_energy_j', 0.0))),
            shooter_17mm_barrel_heat=heat,
        ))
        self.ammo.publish(AllowedShoot(
            projectile_allowance_17mm=ammo,
            remaining_gold_coin=int(robot.get('remaining_gold_coin', 0)),
            fortress_reserve_17mm=fortress_reserve,
        ))

        # The native referee parser tapers to 35% between 90% and 100% of the power limit.
        ratio = chassis_power / power_limit if power_limit > 0 else 0.0
        throttle = max(0.0, min(1.0, (ratio - 0.9) / 0.1))
        self.constraints.publish(Constraints(
            shooter_heat=float(heat), heat_limit=float(heat_limit),
            chassis_power=chassis_power, chassis_power_limit=float(power_limit),
            fire_allowed=(
                progress == Constants.GAME_STAGE_IN_GAME and shooter_enabled and ammo + fortress_reserve > 0
                and (heat_limit <= 0 or heat < max(0, heat_limit - 15)) and ratio <= 1.0
            ),
            speed_scale=1.0 - 0.65 * throttle,
        ))
        color = Constants.COLOR_UNKNOWN
        if 1 <= robot_id < 100:
            color = Constants.COLOR_RED
        elif 100 <= robot_id < 200:
            color = Constants.COLOR_BLUE
        self.self_color.publish(SelfColor(color=color))
