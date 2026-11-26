"""Referee bridge contract using the generated upstream ROS 2 message classes."""
from importlib.util import find_spec
import unittest
from unittest.mock import patch


class Publisher:
    def __init__(self, message_type):
        self.message_type = message_type
        self.messages = []

    def publish(self, message):
        assert isinstance(message, self.message_type)
        self.messages.append(message)


class Node:
    def __init__(self):
        self.topics = {}

    def create_publisher(self, message_type, topic, qos):
        publisher = self.topics[topic] = Publisher(message_type)
        return publisher

    def get_logger(self):
        return self

    def info(self, message):
        pass

    warning = info


@unittest.skipUnless(find_spec('dji_referee_protocol') and find_spec('rclpy'), 'requires installed DJI ROS 2 interfaces')
class RefereeContractTests(unittest.TestCase):
    def test_native_contract_from_authoritative_packet(self):
        from dji_referee_protocol.msg import Constants
        from rmsim_bridge.referee import RefereePublisher

        node = Node()
        with patch('rmsim_bridge.dji_referee.load_package_dlls'), patch('rmsim_bridge.referee.load_package_dlls'):
            referee = RefereePublisher(node)
        native_topics = {
            topic: publisher.message_type.__name__
            for topic, publisher in node.topics.items()
            if publisher.message_type.__module__.startswith('dji_referee_protocol.')
        }
        self.assertEqual(native_topics, {
            '/referee/common/game_status': 'GameStatus',
            '/referee/common/robot_hp': 'RobotHP',
            '/referee/common/robot_performance': 'RobotPerformance',
            '/referee/common/robot_heat': 'RobotHeat',
            '/referee/common/robot_position': 'RobotPosition',
            '/referee/common/allowed_shoot': 'AllowedShoot',
            '/referee/common/rfid_status': 'RFIDStatus',
            '/referee/common/field_event': 'FieldEvent',
            '/referee/common/robot_buff': 'RobotBuff',
            '/referee/common/ground_robot_position': 'GroundRobotPosition',
            '/referee/common/damage_state': 'DamageState',
            '/referee/common/sentry_decision_sync': 'SentryDecisionSync',
            '/referee/parsed/common/constraints': 'Constraints',
            '/referee/parsed/common/self_color': 'SelfColor',
        })

        packet = {'session': 'test-match', 'epoch': 1, 'tick': 20, 'referee': {
            'available': True,
            'game_status': {'game_progress': 4, 'stage_remain_time': 281},
            'all_robot_hp': {'red_7_robot_hp': 371, 'blue_7_robot_hp': 290},
            'robot_status': {
                'robot_id': 107, 'current_hp': 290, 'maximum_hp': 400,
                'shooter_17mm_1_barrel_heat': 125, 'shooter_barrel_heat_limit': 400,
                'projectile_allowance_17mm': 231, 'remaining_gold_coin': 55,
                'pos_x': -3.5, 'pos_y': 1.75, 'yaw_rad': 0.0,
            },
            'event_data': {'supply_zone': 1, 'big_energy': 1},
            'buff': {'cooling_buff': 2},
            'rfid_status': {'friendly_fortress_gain_point': True, 'friendly_supply_zone_exchange': True},
            'sentry_stance': 'DEFENSE', 'chassis_power_w': 95.0, 'chassis_buffer_energy_j': 38.5,
            'chassis_powered': True, 'shooter_locked': False,
        }}
        referee.observe(packet)

        def latest(topic):
            return node.topics[topic].messages[-1]

        self.assertEqual(latest('/referee/common/game_status').stage_remain_time, 281)
        self.assertEqual(latest('/referee/common/robot_hp').red_7_robot_hp, 371)
        self.assertEqual(latest('/referee/common/robot_performance').current_hp, 290)
        self.assertEqual(latest('/referee/common/robot_heat').shooter_17mm_barrel_heat, 125)
        self.assertEqual(latest('/referee/common/robot_position').angle, 90.0)
        self.assertEqual(latest('/referee/common/allowed_shoot').projectile_allowance_17mm, 231)
        self.assertEqual(latest('/referee/common/rfid_status').detected_rfid_bits_low, (1 << 17) | (1 << 20))
        self.assertEqual(latest('/referee/common/field_event').big_energy_mech, Constants.ENERGY_MECH_ACTIVATED)
        self.assertEqual(latest('/referee/common/robot_buff').cooling_buff, 2)
        self.assertEqual(latest('/referee/parsed/common/self_color').color, Constants.COLOR_BLUE)
        self.assertEqual(latest('/referee/common/sentry_decision_sync').sentry_posture, Constants.SENTRY_POSTURE_DEFENSE)
        self.assertTrue(latest('/referee/parsed/common/constraints').fire_allowed)
        self.assertAlmostEqual(latest('/referee/parsed/common/constraints').speed_scale, 0.675)
        self.assertTrue(node.topics['/rmsim/referee/state'].messages)
        self.assertFalse(node.topics['/referee/common/ground_robot_position'].messages)
        damage = node.topics['/referee/common/damage_state'].messages
        self.assertFalse(damage)

        # After the one-step damage flag clears, the latest hit details remain in UE state.
        packet['tick'] += 1
        packet['referee']['robot_status'].update(current_hp=270, armor_id=2, hp_deduction_reason=0)
        referee.observe(packet)
        referee.observe(packet)
        self.assertEqual([(msg.armor_id, msg.damage_type) for msg in damage], [(2, 0)])

        # A new stream with lower HP is not a hit.
        packet.update(session='new-match', tick=0)
        packet['referee']['robot_status']['current_hp'] = 250
        referee.observe(packet)
        self.assertEqual(len(damage), 1)
