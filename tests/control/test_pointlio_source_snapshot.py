"""Point-LIO source snapshots survive source edits and estimator epoch restarts."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'ros2_ws/src/rmsim_bridge'))
from rmsim_bridge.sentry_pointlio_source import pointlio_source_snapshot,pointlio_tuning


def fixture():
    return {'point_lio':{'ros__parameters':{
        'point_filter_num':4,'filter_size_surf':.15,'filter_size_map':.15,
        'ivox_nearby_type':18,'space_down_sample':True,'check_satu':True,
        'use_imu_as_input':False,'prop_at_freq_of_imu':True,
        'mapping':{'acc_norm':1.0,'satu_acc':3.0,'lidar_meas_cov':.001,
                   'imu_meas_acc_cov':.1,'imu_meas_omg_cov':.1,'ivox_grid_resolution':.5}}}}


class PointLioSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder=Path(temporary.name)
        self.source=self.folder/'vehicle.yaml'
        self.source.write_text(json.dumps(fixture()),encoding='utf-8')

    def test_identity_and_tuning_describe_same_bytes_after_file_changes(self):
        before=self.source.read_bytes()
        snapshot=pointlio_source_snapshot(self.source)
        self.assertEqual(snapshot['sha256'],hashlib.sha256(before).hexdigest())
        self.assertEqual(snapshot['path'],str(self.source.resolve()))
        replacement=fixture()
        replacement['point_lio']['ros__parameters']['point_filter_num']=8
        self.source.write_text(json.dumps(replacement),encoding='utf-8')
        newer=pointlio_source_snapshot(self.source)
        self.assertNotEqual(snapshot['sha256'],newer['sha256'])
        self.assertEqual(snapshot['parameters']['point_filter_num'],4)
        self.assertEqual(newer['parameters']['point_filter_num'],8)
        self.source.unlink()
        self.assertEqual(snapshot['parameters']['point_filter_num'],4)

    def test_saturation_converts_raw_units_and_residual_covariance_stays_si(self):
        in_g=pointlio_tuning(self.source)
        si=fixture()
        si['point_lio']['ros__parameters']['mapping'].update(acc_norm=9.81,satu_acc=29.43)
        self.source.write_text(json.dumps(si),encoding='utf-8')
        in_si=pointlio_tuning(self.source)
        self.assertEqual(in_g,in_si)
        self.assertAlmostEqual(in_si['mapping.satu_acc'],29.43)
        self.assertEqual(in_si['mapping.imu_meas_acc_cov'],.1)

    def test_malformed_nested_mappings_report_the_configuration_field(self):
        cases=[
            ([], 'root must be a mapping'),
            ({'point_lio':[]}, 'point_lio must be a mapping'),
            ({'point_lio':{'ros__parameters':[]}}, 'ros__parameters must be a mapping')]
        malformed=fixture()
        malformed['point_lio']['ros__parameters']['mapping']=[]
        cases.append((malformed,'ros__parameters.mapping must be a mapping'))
        for value,message in cases:
            with self.subTest(message=message):
                self.source.write_text(json.dumps(value),encoding='utf-8')
                with self.assertRaisesRegex(ValueError,message):
                    pointlio_source_snapshot(self.source)

    def test_navigation_only_source_retains_explicit_empty_tuning(self):
        self.source.write_text('controller_server: {}\n',encoding='utf-8')
        snapshot=pointlio_source_snapshot(self.source)
        self.assertEqual(snapshot['parameters'],{})
        self.assertIsNotNone(snapshot['sha256'])
        self.assertEqual(pointlio_source_snapshot(''),{'path':None,'sha256':None,'parameters':{}})

    def test_runner_uses_loaded_snapshot_for_status_and_new_sensor_epochs(self):
        # Exercise the real runner's process lifecycle with a minimal ROS facade.
        # No ROS context, process, clock, or simulator is started by this test.
        source=self.source
        folder=self.folder
        publisher=mock.Mock()
        class FakeNode:
            def __init__(self,name):
                pass
            def declare_parameter(self,name,default):
                return types.SimpleNamespace(value={
                    'sentry_source':str(source),'output_directory':str(folder/'runs')
                }.get(name,default))
            def create_subscription(self,*args,**kwargs):
                return None
            def create_publisher(self,*args,**kwargs):
                return publisher
            def create_timer(self,*args,**kwargs):
                return None
            def get_logger(self):
                return mock.Mock()
            def destroy_node(self):
                return True
        class Message:
            def __init__(self,**kwargs):
                self.__dict__.update(kwargs)
        modules={name:types.ModuleType(name) for name in (
            'rclpy','rclpy.node','rclpy.clock','rclpy.qos','std_msgs','std_msgs.msg',
            'ament_index_python','ament_index_python.packages')}
        modules['rclpy.node'].Node=FakeNode
        modules['rclpy.clock'].Clock=lambda **kwargs:None
        modules['rclpy.clock'].ClockType=types.SimpleNamespace(STEADY_TIME=1)
        modules['rclpy.qos'].QoSProfile=lambda **kwargs:None
        modules['std_msgs.msg'].String=Message
        modules['ament_index_python.packages'].get_package_prefix=lambda name:str(folder)
        file=ROOT/'ros2_ws/src/rmsim_bridge/rmsim_bridge/point_lio_runner.py'
        spec=importlib.util.spec_from_file_location('rmsim_bridge._snapshot_test_runner',file)
        module=importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules,modules):
            spec.loader.exec_module(module)
        original=pointlio_source_snapshot(source)
        runner=module.PointLioRunner()
        try:
            # A live edit must neither change status nor alter a restarted estimator.
            source.write_text('point_lio: [invalid]\n',encoding='utf-8')
            pose={'position_m':[0.,0.,0.],'rotation_xyzw':[0.,0.,0.,1.]}
            sensor={'parent_frame_id':'aim_sentry/base_link','pose_in_base':pose}
            runner.state={'session':'fixture','epoch':0,'connected':True,'state_fresh':True,
                'lidar':dict(sensor,id='lidar',frame_hz=10.),
                'imu':{'gravity_z_mps2':-9.8,'profiles':[dict(sensor,id='imu',rate_hz=200.)]}}
            runner.received=time.monotonic()
            child=mock.Mock(pid=12345)
            child.poll.return_value=None
            with mock.patch.object(module.subprocess,'Popen',return_value=child) as launch:
                with mock.patch.object(module,'pointlio_source_snapshot',side_effect=AssertionError('source reopened')):
                    with mock.patch.object(module,'pointlio_tuning',side_effect=AssertionError('source reparsed')):
                        runner.poll()
                        runner.state['epoch']=1
                        runner.poll()
                        runner.state['epoch']=2
                        runner.state['lidar'].update(
                            profile_schema='rm_lidar_runtime_v3',
                            physical_model={'query_group_stride':4})
                        runner.poll()
                self.assertEqual(launch.call_count,3)
            generated=[json.loads(p.read_text())['/**']['ros__parameters']
                       for p in sorted(runner.output.glob('run-*.yaml'))]
            self.assertEqual(len(generated),3)
            self.assertEqual(generated[0],generated[1])
            for key,value in original['parameters'].items():
                self.assertEqual(generated[0][key],value)
            self.assertEqual(generated[2],dict(generated[0],point_filter_num=1))
            status=json.loads(publisher.publish.call_args.args[0].data)
            self.assertEqual(status['source_identity']['sha256'],original['sha256'])
            self.assertEqual(status['source_filtering'],original['parameters'])
        finally:
            runner.destroy_node()


if __name__=='__main__':
    unittest.main()
