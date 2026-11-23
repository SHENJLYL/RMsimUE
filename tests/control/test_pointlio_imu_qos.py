"""Selected Point-LIO receive history reaches native runner configuration unchanged."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'ros2_ws/src/rmsim_bridge'))
from rmsim_bridge.sentry_pointlio_source import pointlio_source_snapshot

helper_path = ROOT/'tests/control/test_pointlio_source_snapshot.py'
helper_spec = importlib.util.spec_from_file_location('_pointlio_snapshot_contract_fixture', helper_path)
helper = importlib.util.module_from_spec(helper_spec)
helper_spec.loader.exec_module(helper)


class PointLioImuQosTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.source = Path(folder.name)/'selected_vehicle.yaml'

    def snapshot(self, data):
        self.source.write_text(json.dumps(data), encoding='utf-8')
        return pointlio_source_snapshot(self.source)

    def test_legacy_default_and_explicit_depth_change_only_receive_history(self):
        source = helper.fixture()
        legacy = self.snapshot(source)['parameters']
        self.assertEqual(legacy['common.imu_qos_depth'], 5)
        source['point_lio']['ros__parameters']['common'] = {'imu_qos_depth': 128}
        configured = self.snapshot(source)['parameters']
        changed = {key for key in legacy.keys() | configured.keys()
                   if legacy.get(key) != configured.get(key)}
        self.assertEqual(changed, {'common.imu_qos_depth'})
        self.assertEqual(configured['common.imu_qos_depth'], 128)
        # Physical units and the estimator's selected tuning survive the transport change.
        self.assertAlmostEqual(configured['mapping.satu_acc'], 29.43)
        self.assertEqual(configured['mapping.imu_meas_acc_cov'], .1)

    def test_depth_contract_accepts_integer_bounds_and_rejects_ambiguous_values(self):
        for depth in (1, 128, 4096):
            source = helper.fixture()
            source['point_lio']['ros__parameters']['common'] = {'imu_qos_depth': depth}
            self.assertEqual(self.snapshot(source)['parameters']['common.imu_qos_depth'], depth)
        for depth in (True, False, 0, -1, 1.0, 128.0, '128', None, 4097):
            with self.subTest(depth=depth):
                source = helper.fixture()
                source['point_lio']['ros__parameters']['common'] = {'imu_qos_depth': depth}
                with self.assertRaisesRegex(ValueError, 'common.imu_qos_depth'):
                    self.snapshot(source)

    def test_malformed_common_mapping_is_reported_at_source_boundary(self):
        for common in (None, [], 128, 'invalid'):
            with self.subTest(common=common):
                source = helper.fixture()
                source['point_lio']['ros__parameters']['common'] = common
                with self.assertRaisesRegex(ValueError, 'ros__parameters.common must be a mapping'):
                    self.snapshot(source)

    def test_navigation_only_source_keeps_empty_estimator_override(self):
        self.assertEqual(self.snapshot({'controller_server': {'ros__parameters': {}}})['parameters'], {})

    def test_selected_depth_is_in_real_runner_params_and_survives_epoch_restart(self):
        # Reuse the real runner lifecycle harness: it reads a selected source,
        # starts a mocked child with an actual generated ROS parameter file,
        # replaces the YAML with invalid content, then restarts a sensor epoch.
        # Its assertions compare every snapshotted value against both native
        # parameter files and published source identity without reparsing.
        source = helper.fixture()
        source['point_lio']['ros__parameters']['common'] = {'imu_qos_depth': 128}
        self.assertEqual(self.snapshot(source)['parameters']['common.imu_qos_depth'], 128)
        case = helper.PointLioSnapshotTests(
            'test_runner_uses_loaded_snapshot_for_status_and_new_sensor_epochs')
        with mock.patch.object(helper, 'fixture', side_effect=lambda: copy.deepcopy(source)):
            case.setUp()
            try:
                case.test_runner_uses_loaded_snapshot_for_status_and_new_sensor_epochs()
            finally:
                case.doCleanups()


if __name__ == '__main__':
    unittest.main()
