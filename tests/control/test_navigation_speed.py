"""Pin real/simulation Nav2 speed, smoother and avoidance to sentry_ws."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import yaml

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'ros2_ws/src/rmsim_bridge'))
from rmsim_bridge.navigation_config import prepare
from rmsim_bridge.sentry_navigation_source import source_profile, NAVIGATION_BLOCKS
from rmsim_bridge.sentry_pointlio_source import pointlio_tuning

class SentryNavigationSourceTests(unittest.TestCase):
    def setUp(self):
        self.original=ROOT/'third_party/sentry_nav_original/pb2025_nav_bringup/config'
        self.source=ROOT/'ros2_ws/src/pb2025_nav_bringup/config'
        self.reference=self.source/'reality/nav2_params.yaml'
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder=Path(self.temp.name)
        self.directory=folder
        self.map=folder/'map.yaml'
        self.map.write_text('image: map.pgm\n')
        (folder/'metadata.json').write_text(json.dumps({'map_id':'fixture','source_md5':'1'*32}))

    def test_original_source_unchanged_and_aim_tuned_original_algorithm(self):
        self.assertEqual((self.source/'reality/nav2_params.yaml').read_bytes(),
                         (self.original/'reality/nav2_params.yaml').read_bytes())
        original=yaml.safe_load((self.original/'simulation/nav2_params.yaml').read_text(encoding='utf-8'))
        patched=yaml.safe_load((self.source/'simulation/nav2_params.yaml').read_text(encoding='utf-8'))
        follow=original['controller_server']['ros__parameters']['FollowPath']
        follow['v_linear_max']=5.0
        follow['min_approach_linear_velocity']=0.05
        follow['approach_velocity_scaling_dist']=2.0
        follow['use_interpolation']=True
        follow['max_lookahead_dist']=2.0
        smooth=original['velocity_smoother']['ros__parameters']
        smooth['max_velocity']=[5.0,2.0,3.0]
        smooth['min_velocity']=[-1.5,-2.0,-3.0]
        smooth['max_accel']=[0.8,0.8,3.0]
        smooth['max_decel']=[-3.0,-3.0,-5.0]
        # The selected source explicitly owns native IMU receive history.
        original['point_lio']['ros__parameters']['common']['imu_qos_depth']=128
        original['controller_server']['ros__parameters']['general_goal_checker']['xy_goal_tolerance']=.05
        # Localization is copied from the previously effective reality reference;
        # this ownership change preserves its estimator and scan values.
        reality=yaml.safe_load(self.reference.read_text(encoding='utf-8'))
        for node in ('amcl','pointcloud_to_laserscan'):
            original[node]=reality[node]
        self.assertEqual(original,patched)
        manual=json.loads((ROOT/'assets/vehicles/aim_sentry/drive-profile.json').read_text())['control']
        self.assertEqual(patched['controller_server']['ros__parameters']['FollowPath']['v_linear_max'],manual['MaxForwardSpeedMps'])
        self.assertEqual(smooth['max_velocity'],[manual['MaxForwardSpeedMps'],2.0,3.0])
        self.assertEqual(smooth['min_velocity'],[-1.5,-2.0,-3.0])
        # Only the forward ceiling follows the current manual capability.
        # Other directions retain the source limits within the physical envelope.
        self.assertLessEqual(smooth['max_velocity'][1],manual['MaxLateralSpeedMps'])
        self.assertLessEqual(-smooth['min_velocity'][0],manual['MaxReverseSpeedMps'])
        self.assertLessEqual(-smooth['min_velocity'][1],manual['MaxLateralSpeedMps'])
        self.assertLessEqual(max(smooth['max_velocity'][2],-smooth['min_velocity'][2]),manual['GyroMaxRadS'])

    def test_sentry_profiles_match_original_controller_and_smoother(self):
        for name,speed,acceleration,deceleration in [('simulation',5.0,0.8,3.0),('reality',1.0,1.5,1.5)]:
            with self.subTest(mode=name):
                cfg,_=prepare(self.reference,self.map,'aim_sentry',[.6,-.4,.15],name)
                raw=yaml.safe_load((self.source/name/'nav2_params.yaml').read_text(encoding='utf-8'))
                self.assertEqual(cfg['controller_server']['ros__parameters']['FollowPath'],
                                 raw['controller_server']['ros__parameters']['FollowPath'])
                self.assertEqual(cfg['controller_server']['ros__parameters']['general_goal_checker'],
                                 raw['controller_server']['ros__parameters']['general_goal_checker'])
                native_smoother=dict(cfg['velocity_smoother']['ros__parameters'])
                upstream_smoother=dict(raw['velocity_smoother']['ros__parameters'])
                # Simulated time must be enabled in UE, even for original reality presets.
                self.assertTrue(native_smoother.pop('use_sim_time'))
                upstream_smoother.pop('use_sim_time')
                self.assertEqual(native_smoother,upstream_smoother)
                self.assertEqual(cfg['controller_server']['ros__parameters']['FollowPath']['v_linear_max'],speed)
                self.assertEqual(cfg['velocity_smoother']['ros__parameters']['max_accel'][0],acceleration)
                self.assertEqual(cfg['velocity_smoother']['ros__parameters']['max_decel'][0],-deceleration)
                self.assertEqual(cfg['controller_server']['ros__parameters']['FollowPath']['reduction_ratio_at_high_curvature'],.5)
                for node in ('local_costmap','global_costmap'):
                    native=cfg[node][node]['ros__parameters']
                    orig=raw[node][node]['ros__parameters']
                    self.assertEqual(native['plugins'],orig['plugins'])
                    self.assertEqual(native['inflation_layer'],orig['inflation_layer'])
                    self.assertEqual(native['intensity_voxel_layer']['plugin'],
                                     orig['intensity_voxel_layer']['plugin'])
                    self.assertEqual(native['footprint'],'[[-0.26,-0.26],[-0.26,0.26],[0.26,0.26],[0.26,-0.26]]')
                self.assertEqual(cfg['amcl']['ros__parameters']['global_frame_id'],'aim_sentry/map')


    def test_external_vehicle_navigation_yaml_is_source_of_truth(self):
        user_source=self.directory/'other_vehicle.yaml'
        data=yaml.safe_load((self.source/'simulation/nav2_params.yaml').read_text(encoding='utf-8'))
        data['controller_server']['ros__parameters']['FollowPath']['v_linear_max']=1.7
        data['velocity_smoother']['ros__parameters']['max_velocity']=[1.7,1.2,2.0]
        data['velocity_smoother']['ros__parameters']['min_velocity']=[-1.1,-1.2,-2.0]
        user_source.write_text(yaml.safe_dump(data),encoding='utf-8')
        cfg,_=prepare(self.reference,self.map,'aim_sentry',[.6,-.4,.15],'simulation',str(user_source))
        self.assertEqual(cfg['controller_server']['ros__parameters']['FollowPath']['v_linear_max'],1.7)
        self.assertEqual(cfg['velocity_smoother']['ros__parameters']['max_velocity'][:2],[1.7,1.2])
        self.assertEqual(cfg['velocity_smoother']['ros__parameters']['min_velocity'][:2],[-1.1,-1.2])
        self.assertEqual(cfg['controller_server']['ros__parameters']['FollowPath']['reduction_ratio_at_high_curvature'],.5)

    def test_pointlio_filters_follow_the_same_source_yaml(self):
        reference=yaml.safe_load((self.source/'simulation/nav2_params.yaml').read_text(encoding='utf-8'))['point_lio']['ros__parameters']
        tuning=pointlio_tuning(self.source/'simulation/nav2_params.yaml')
        for name in ('point_filter_num','filter_size_surf','filter_size_map','ivox_nearby_type','space_down_sample'):
            self.assertEqual(tuning[name],reference[name])
        self.assertEqual(tuning['point_filter_num'],8)
        self.assertEqual(tuning['filter_size_surf'],.2)
        self.assertEqual(tuning['filter_size_map'],.2)
        self.assertAlmostEqual(tuning['mapping.satu_acc'],30.0)
        self.assertEqual(tuning['mapping.lidar_meas_cov'],.001)
        self.assertEqual(tuning['mapping.imu_meas_acc_cov'],.1)
        self.assertFalse(tuning['use_imu_as_input'])
        self.assertAlmostEqual(pointlio_tuning(self.source/'reality/nav2_params.yaml')['mapping.satu_acc'],4*9.81)
        self.assertEqual(pointlio_tuning(''),{})

    def test_historical_legacy_diagnostics_preserved(self):
        cfg,_=prepare(self.reference,self.map,'aim_sentry',[.6,-.4,.15])
        self.assertEqual(cfg['controller_server']['ros__parameters']['FollowPath']['v_linear_max'],.35)
        self.assertEqual(cfg['velocity_smoother']['ros__parameters']['max_velocity'][:2],[.35,.35])
        self.assertEqual(cfg['controller_server']['ros__parameters']['general_goal_checker']['xy_goal_tolerance'],.10)

    def test_unsupported_profile_does_not_resolve_arbitrary_files(self):
        for name in ('../external','foo','SIMULATION',None):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):source_profile(self.reference,name)

    def test_simulator_source_is_immutable_during_selection(self):
        before=self.reference.read_bytes()
        selected,_=source_profile(self.reference,'simulation')
        selected['controller_server']['ros__parameters']['FollowPath']['v_linear_max']=100
        selected_again,_=source_profile(self.reference,'simulation')
        self.assertEqual(selected_again['controller_server']['ros__parameters']['FollowPath']['v_linear_max'],5.0)
        self.assertEqual(self.reference.read_bytes(),before)

if __name__=='__main__':unittest.main()
