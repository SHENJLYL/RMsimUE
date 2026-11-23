"""Physical-lane and phase-isolation checks for the staged Nav2 speed gate."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools/integration'))
import probe_navigation_top_speed as gate


class StagedNavigationSpeedTests(unittest.TestCase):
    def setUp(self):
        self.scene_path=ROOT/'assets/maps/import_lab/map.json'
        self.scene=json.loads(self.scene_path.read_text(encoding='utf-8-sig'))
        self.radius=2**.5*.26+.10

    def test_real_seventeen_metre_lane_preserves_source_acceleration(self):
        lane=gate.speed_lane_contract(self.scene,gate.STAGING_GOAL,gate.GOAL,self.radius,5.,.8,2.)
        self.assertEqual(lane['length_m'],17.)
        self.assertAlmostEqual(lane['source_theoretical_minimum_length_m'],11.2)
        self.assertEqual(lane['length_before_approach_m'],15.)
        self.assertAlmostEqual(lane['wall_clearance_m'],10-.2-self.radius-8.5)
        obstacles={v['id']:v['additional_box_padding_clearance_m'] for v in lane['obstacle_clearances']}
        self.assertAlmostEqual(obstacles['probe_box'],1-.3-self.radius,places=8)
        self.assertAlmostEqual(obstacles['tall_box'],1.4-.4-self.radius,places=8)
        with self.assertRaisesRegex(ValueError,'cannot achieve'):
            gate.speed_lane_contract(self.scene,(0.,0.),gate.GOAL,self.radius,5.,.8,2.)

    def test_collision_and_wall_failures_are_rejected(self):
        blocked=copy.deepcopy(self.scene)
        blocked['obstacles'].append({'id':'blocking_fixture','center_m':[0.,0.,.5],
                                     'size_m':[.5,.5,1.],'yaw_rad':.3})
        with self.assertRaisesRegex(ValueError,'blocking_fixture'):
            gate.lane_geometry(blocked,gate.STAGING_GOAL,gate.GOAL,self.radius)
        with self.assertRaisesRegex(ValueError,'floor/boundary'):
            gate.lane_geometry(self.scene,(-9.5,0.),gate.GOAL,self.radius)

    def test_actual_staging_position_is_used_for_measured_lane(self):
        actual=(-8.4,.15)
        evidence=gate.staging_arrival_evidence(actual,4,.01,.6)
        self.assertTrue(evidence['within_position_tolerance'])
        lane=gate.speed_lane_contract(self.scene,actual,gate.GOAL,self.radius,5.,.8,2.)
        self.assertEqual(lane['start_m'],list(actual))
        self.assertNotEqual(lane['length_m'],17.)
        self.assertGreater(lane['length_before_approach_m'],11.2)

    def test_staging_requires_original_success_position_and_full_stop(self):
        good=gate.staging_arrival_evidence((-8.5,0.,.04),4,.01,.5)
        keys=('action_succeeded','within_position_tolerance','stopped')
        self.assertTrue(all(good[key] for key in keys))
        cases=[((-8.5,0.),5,.01,.5),((-8.249,0.),4,.01,.5),
               ((-8.5,0.),4,.05,.5),((-8.5,0.),4,.01,.499)]
        for args in cases:
            with self.subTest(args=args):
                evidence=gate.staging_arrival_evidence(*args)
                self.assertFalse(all(evidence[key] for key in keys))

    def test_formal_statistics_exclude_staging_and_late_headers(self):
        rows=[{'phase':'staging','stamp':9.,'speed_mps':6.},
              {'phase':'staging','stamp':10.1,'speed_mps':6.},
              {'phase':'formal','stamp':9.99,'speed_mps':6.},
              {'phase':'formal','stamp':10.1,'speed_mps':4.1},
              {'phase':'formal','stamp':10.4,'speed_mps':4.2},
              {'stamp':10.5,'speed_mps':6.}]
        selected=gate.formal_rows(rows,10.)
        self.assertEqual([r['stamp'] for r in selected],[10.1,10.4])
        self.assertEqual(max(r['speed_mps'] for r in selected),4.2)

    def test_current_source_contract_retains_spawn_and_separate_staging(self):
        # Synthetic session metadata is confined to a temporary unit-test folder.
        with tempfile.TemporaryDirectory() as temp:
            session=Path(temp)
            scene=session/'scene.json'
            scene.write_bytes(self.scene_path.read_bytes())
            source=ROOT/'ros2_ws/src/pb2025_nav_bringup/config/simulation/nav2_params.yaml'
            drive=ROOT/'assets/vehicles/aim_sentry/drive-profile.json'
            provenance={'scene':{'scene':'import_lab','scene_file':str(scene),'scene_sha256':gate.digest(scene)},
                        'navigation_source':str(source),'source_control_config_sha256':gate.digest(source),
                        'drive_profile_sha256':gate.digest(drive),'sim_step_seconds':.005}
            (session/'runtime-provenance.json').write_text(json.dumps(provenance),encoding='utf-8')
            contract=gate.source_contract(session)
            self.assertEqual(contract['spawn_start'],[0.,0.])
            self.assertEqual(contract['start'],[-8.5,0.])
            self.assertEqual(contract['formal_lane']['length_m'],17.)
            self.assertEqual(contract['source_cap_mps'],5.)
            self.assertEqual(contract['speed_threshold_mps'],4.)
            self.assertEqual(contract['source_accel_mps2'],.8)
            self.assertEqual(contract['source_decel_mps2'],3.)
            scene.write_text('{}',encoding='utf-8')
            with self.assertRaises((ValueError,KeyError)):
                gate.source_contract(session)


if __name__=='__main__':
    unittest.main()
