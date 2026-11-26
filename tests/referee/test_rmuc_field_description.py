import importlib.util
import json
import math
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / 'ros2_ws/src/rmsim_bridge'
MAP = ROOT / 'assets/maps/rmuc2027'
sys.path.insert(0, str(BRIDGE))
from rmsim_bridge.field_joints import BASE_JOINT_NAMES, base_joint_positions, description_joints


class FieldDescriptionTests(unittest.TestCase):
    def test_generated_urdf_and_live_joint_contract(self):
        spec = importlib.util.spec_from_file_location('rmuc_field_export', ROOT / 'tools/maps/export_rmuc_field_description.py')
        exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exporter)
        armor = json.loads((MAP / 'base-armor-kinematics.json').read_text(encoding='utf-8-sig'))
        zones = json.loads((MAP / 'field-zones.json').read_text(encoding='utf-8-sig'))
        installed = (BRIDGE / 'description/rmuc2027_field.urdf').read_text(encoding='utf-8')
        self.assertEqual(installed, exporter.description(armor, zones))
        fixed, moving = description_joints(installed)
        self.assertEqual(len(fixed), 12)
        self.assertEqual(tuple(moving), BASE_JOINT_NAMES)
        robot = ET.fromstring(installed)
        self.assertTrue(all(link.find('collision') is None for link in robot.findall('link')))
        for panel in armor['panels']:
            prefix = f"rmuc2027_{panel['side']}_base_panel_{panel['index']}"
            slide, hinge = moving[prefix + '_slide'], moving[prefix + '_hinge']
            closed, _ = slide.pose(0.)
            opened, _ = slide.pose(panel['slide_open_m'])
            self.assertAlmostEqual(opened[2] - closed[2], -.075)
            self.assertAlmostEqual(math.hypot(opened[0] - closed[0], opened[1] - closed[1]), .25)
            _, orientation = hinge.pose(panel['hinge_open_rad'])
            self.assertAlmostEqual(2 * math.acos(orientation[3]), math.radians(6))

    def test_legacy_and_invalid_bridge_packets(self):
        self.assertIsNone(base_joint_positions({}))
        field = {'base_armor_meshes_bound': True, 'base_armor_joint_positions': [0.] * 12}
        self.assertEqual(base_joint_positions({'field_devices': field}), [0.] * 12)
        field['base_armor_joint_positions'][-1] = float('nan')
        self.assertIsNone(base_joint_positions({'field_devices': field}))


if __name__ == '__main__':
    unittest.main()
