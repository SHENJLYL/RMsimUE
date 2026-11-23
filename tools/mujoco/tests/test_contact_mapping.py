"""Verify the source UE Average coefficients on real MuJoCo geom/flex contacts."""
import copy
import json
from pathlib import Path
import sys
import unittest
import mujoco

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'tools/mujoco'))
from prepare_model import source_average_friction


class SourceContactMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile=json.loads((ROOT/'assets/vehicles/aim_sentry/drive-profile.json').read_text())
        cls.config=json.loads((ROOT/'assets/vehicles/aim_sentry/mujoco/backend.json').read_text())

    def test_average_uses_actual_ue_float_material_values(self):
        self.assertEqual(source_average_friction(self.profile,self.config,True),1.2499999701976776)
        self.assertEqual(source_average_friction(self.profile,self.config,False),.5999999940395355)

    def test_mujoco_contact_selects_source_average_on_geom_and_flex(self):
        static=self.config['contact']['static_sliding_friction']
        for wheel in (False,True):
            expected=source_average_friction(self.profile,self.config,wheel)
            for flex in (False,True):
                with self.subTest(wheel=wheel,flex=flex):
                    ground='' if flex else f'<geom name="ground" type="plane" size="1 1 .1" priority="0" condim="3" friction="{static} 0 0" solref=".002 1"/>'
                    deform=(f'<deformable><flex name="ground_flex" body="world" dim="2" radius=".0001" vertex="-1 -1 0 1 -1 0 1 1 0 -1 1 0" element="0 1 2 0 2 3"><contact selfcollide="none" priority="0" condim="3" friction="{static} 0 0" solref=".002 1"/></flex></deformable>') if flex else ''
                    xml=f'<mujoco><option timestep=".001" integrator="implicitfast"/><worldbody>{ground}<body pos="0 0 .099"><freejoint/><geom type="sphere" size=".1" mass="1" priority="1" condim="3" friction="{expected} 0 0" solref=".002 1"/></body></worldbody>{deform}</mujoco>'
                    model=mujoco.MjModel.from_xml_string(xml);data=mujoco.MjData(model)
                    mujoco.mj_forward(model,data)
                    self.assertGreater(data.ncon,0)
                    for contact in data.contact[:data.ncon]:
                        self.assertEqual(contact.dim,3)
                        self.assertAlmostEqual(contact.friction[0],expected,places=14)
                        self.assertAlmostEqual(contact.friction[1],expected,places=14)

    def test_unreviewed_surface_or_combine_change_is_rejected(self):
        for section,key,value in [('contact','static_sliding_friction',.8),
                                  ('contact','condim',6),
                                  ('contact','rolling_friction',.001),
                                  ('contact_mapping','source_friction_combine','Max')]:
            with self.subTest(section=section,key=key):
                config=copy.deepcopy(self.config);config[section][key]=value
                with self.assertRaises(ValueError):
                    source_average_friction(self.profile,config,True)


if __name__=='__main__':
    unittest.main()
