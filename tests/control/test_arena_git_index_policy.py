"""No broad unignoring of Unreal generated content for small CC0 arena."""
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('index_audit',ROOT/'tools/repository/audit_index.py')
MODULE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MODULE)

class ArenaIndexTests(unittest.TestCase):
    def test_exact_six_cc0_meshes(self):
        for stem in ('banner','border_straight','column','stairs','wall','wall_gate'):
            path='unreal/RMSim/Content/RMSim/Maps/rmuc2027/Arena/Kenney/'+stem+'.uasset'
            self.assertTrue(MODULE.approved_arena_asset(path,90000))
    def test_exact_eight_mat_palette(self):
        for stem in ('Facade','Seats','UpperSeats','Supports','Roof','AccentRed','AccentBlue','AccentLight'):
            path='unreal/RMSim/Content/RMSim/Maps/rmuc2027/Arena/Materials/Mat_'+stem+'.uasset'
            self.assertTrue(MODULE.approved_arena_asset(path,90000))
    def test_generated_assets_and_oversized_package_stay_rejected(self):
        for path in ('unreal/RMSim/Content/RMSim/Vehicles/aim_sentry/body.uasset',
                     'unreal/RMSim/Content/RMSim/Maps/rmuc2027/Arena/Kenney/evil.uasset',
                     'unreal/RMSim/Content/RMSim/Maps/rmuc2027/Arena/Materials/Mat_Unknown.uasset',
                     'unreal/RMSim/Content/RMSim/Maps/rmuc2027/Arena/Kenney/wall.exe'):
            self.assertFalse(MODULE.approved_arena_asset(path,9000))
        good='unreal/RMSim/Content/RMSim/Maps/rmuc2027/Arena/Kenney/wall.uasset'
        self.assertFalse(MODULE.approved_arena_asset(good,150000))

if __name__=='__main__':unittest.main()
