"""SI conversion preserves the pinned physical saturation limit and estimator input."""
import math
from pathlib import Path
import re
import sys
import unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'ros2_ws/src/rmsim_bridge'))
from rmsim_bridge.pointlio_units import si_acceleration_parameters,STANDARD_GRAVITY

class PointLioUnitsTests(unittest.TestCase):
    def test_pinned_upstream_contract(self):
        text=(ROOT/'ros2_ws/src/point_lio/config/mid360.yaml').read_text()
        self.assertEqual(float(re.search(r'\bsatu_acc:\s*([\d.]+)',text)[1]),3.)
        self.assertEqual(float(re.search(r'\bacc_norm:\s*([\d.]+)',text)[1]),1.)
    def test_both_raw_parameters_convert(self):
        p=si_acceleration_parameters()
        self.assertEqual(p['mapping.acc_norm'],9.81)
        self.assertAlmostEqual(p['mapping.satu_acc'],29.43)
    def test_static_gravity_at_all_mount_angles_is_not_saturated(self):
        p=si_acceleration_parameters()
        for degrees in range(0,181,5):
            angle=math.radians(degrees)
            for a in (9.8*math.sin(angle),9.8*math.cos(angle)):
                self.assertLess(abs(a),.99*p['mapping.satu_acc'])
    def test_original_saturation_decision_is_preserved(self):
        p=si_acceleration_parameters()
        for g in (-4.,-3.,-2.9,-1.,0.,1.,2.9,3.,4.):
            self.assertEqual(abs(g)>=.99*3.,abs(g*STANDARD_GRAVITY)>=.99*p['mapping.satu_acc'])
    def test_normalized_measurement_matches_original(self):
        for g in (-1.,0.,.5,1.,2.):
            before=g*STANDARD_GRAVITY
            after=(g*STANDARD_GRAVITY)*STANDARD_GRAVITY/si_acceleration_parameters()['mapping.acc_norm']
            self.assertAlmostEqual(before,after)
    def test_invalid_range_rejected(self):
        for value in (True,0,-1,float('nan'),float('inf'),'3'):
            with self.assertRaises(ValueError):si_acceleration_parameters(value)

if __name__=='__main__':unittest.main()
