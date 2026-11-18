"""Exercise source-owned goal/localization configuration through the real adapter."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ros2_ws/src/rmsim_bridge'))
from rmsim_bridge.navigation_config import prepare
from rmsim_bridge.sentry_navigation_source import source_profile, OPTIONAL_LOCALIZATION_BLOCKS


class NavigationGoalSourceTests(unittest.TestCase):
    def setUp(self):
        self.config = ROOT / 'ros2_ws/src/pb2025_nav_bringup/config'
        self.reference = self.config / 'reality/nav2_params.yaml'
        self.source = self.config / 'simulation/nav2_params.yaml'
        self.data = yaml.safe_load(self.source.read_text(encoding='utf-8'))
        self.reference_data = yaml.safe_load(self.reference.read_text(encoding='utf-8'))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.map = self.folder / 'map.yaml'
        self.map.write_text('image: map.pgm\n', encoding='utf-8')
        (self.folder / 'metadata.json').write_text(
            json.dumps({'map_id': 'fixture', 'source_md5': '1' * 32}), encoding='utf-8')

    def external(self, data):
        path = self.folder / 'vehicle-navigation.yaml'
        path.write_text(yaml.safe_dump(data), encoding='utf-8')
        return path

    def adapted(self, path=''):
        return prepare(self.reference, self.map, 'vehicle_beta',
                       [.6, -.4, .15], 'simulation', str(path))[0]

    def test_working_goal_and_localization_values_are_explicit_source_choices(self):
        checker = self.data['controller_server']['ros__parameters']['general_goal_checker']
        self.assertEqual(checker['xy_goal_tolerance'], .05)
        self.assertEqual(self.adapted()['controller_server']['ros__parameters']['general_goal_checker'],
                         checker)
        for name in OPTIONAL_LOCALIZATION_BLOCKS:
            with self.subTest(node=name):
                self.assertEqual(self.data[name], self.reference_data[name])

    def test_external_goal_and_localization_survive_boundary_adaptation(self):
        data = copy.deepcopy(self.data)
        checker = data['controller_server']['ros__parameters']['general_goal_checker']
        checker.update(xy_goal_tolerance=.037, yaw_goal_tolerance=.22, stateful=False)
        amcl = data['amcl']['ros__parameters']
        amcl.update(sigma_hit=.072, max_beams=77, update_min_d=.012,
                    base_frame_id='source_base', global_frame_id='source_map',
                    use_sim_time=False)
        scan = data['pointcloud_to_laserscan']['ros__parameters']
        scan.update(min_height=.21, max_height=1.7, min_intensity=.14,
                    angle_increment=.006, target_frame='source_scan', use_sim_time=False)
        path = self.external(data)
        before = path.read_bytes()
        selected, identity = source_profile(self.reference, 'simulation', str(path))
        cfg = self.adapted(path)
        self.assertEqual(cfg['controller_server']['ros__parameters']['general_goal_checker'], checker)
        native_amcl = cfg['amcl']['ros__parameters']
        for name in ('sigma_hit', 'max_beams', 'update_min_d'):
            self.assertEqual(native_amcl[name], amcl[name])
        native_scan = cfg['pointcloud_to_laserscan']['ros__parameters']
        for name in ('min_height', 'max_height', 'min_intensity', 'angle_increment'):
            self.assertEqual(native_scan[name], scan[name])
        self.assertEqual(native_amcl['base_frame_id'], 'vehicle_beta/base_stabilized')
        self.assertEqual(native_amcl['global_frame_id'], 'vehicle_beta/map')
        self.assertEqual(native_scan['target_frame'], 'vehicle_beta/base_stabilized')
        self.assertTrue(native_amcl['use_sim_time'])
        self.assertTrue(native_scan['use_sim_time'])
        self.assertEqual(identity['source_sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(selected['amcl'], data['amcl'])

    def test_older_vehicle_without_optional_blocks_retains_reference_localization(self):
        data = copy.deepcopy(self.data)
        for name in OPTIONAL_LOCALIZATION_BLOCKS:
            data.pop(name)
        path = self.external(data)
        selected, _ = source_profile(self.reference, 'simulation', str(path))
        native = self.adapted(path)
        legacy, _ = prepare(self.reference, self.map, 'vehicle_beta', [.6, -.4, .15])
        for name in OPTIONAL_LOCALIZATION_BLOCKS:
            with self.subTest(node=name):
                self.assertNotIn(name, selected)
                self.assertEqual(native[name], legacy[name])

    def test_each_optional_block_can_override_independently(self):
        for name, parameter, value in [
                ('amcl', 'sigma_hit', .081),
                ('pointcloud_to_laserscan', 'min_height', .17)]:
            with self.subTest(node=name):
                data = copy.deepcopy(self.data)
                for other in OPTIONAL_LOCALIZATION_BLOCKS:
                    if other != name:
                        data.pop(other)
                data[name]['ros__parameters'][parameter] = value
                cfg = self.adapted(self.external(data))
                self.assertEqual(cfg[name]['ros__parameters'][parameter], value)
                unchanged = next(other for other in OPTIONAL_LOCALIZATION_BLOCKS if other != name)
                baseline = self.adapted()
                self.assertEqual(cfg[unchanged], baseline[unchanged])

    def test_present_malformed_optional_blocks_fail_with_node_identity(self):
        invalid = [None, [], 'invalid', {}, {'ros__parameters': None},
                   {'ros__parameters': []}, {'ros__parameters': 7}]
        for name in OPTIONAL_LOCALIZATION_BLOCKS:
            for block in invalid:
                with self.subTest(node=name, block=block):
                    data = copy.deepcopy(self.data)
                    data[name] = block
                    path = self.external(data)
                    with self.assertRaisesRegex(ValueError, name + '.*ros__parameters'):
                        self.adapted(path)

    def test_optional_selection_is_a_copy_and_does_not_rewrite_vehicle_source(self):
        path = self.external(self.data)
        before = path.read_bytes()
        first, _ = source_profile(self.reference, 'simulation', str(path))
        first['amcl']['ros__parameters']['sigma_hit'] = 99
        first['pointcloud_to_laserscan']['ros__parameters']['min_height'] = 99
        second, _ = source_profile(self.reference, 'simulation', str(path))
        for name in OPTIONAL_LOCALIZATION_BLOCKS:
            self.assertEqual(second[name], self.data[name])
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
