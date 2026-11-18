"""Boundary tests for the narrow original-map water color contract."""
import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools' / 'unreal'))
import wt_water as water


def fixture():
    return dict(id=water.MAP_ID, source_sha256=water.SOURCE_MAP_SHA256,
        materials=[dict(id=water.MATERIAL_ID, shader='water_aces', parameters='', texture_slots=[None]*11)],
        meshes=[dict(id=water.MESH_ID, sha256=water.MESH_SHA256, file='meshes/world_static_000.fbx',
                     material_slots=['a','b','c','d',water.MATERIAL_ID])])


class WaterContractTests(unittest.TestCase):
    def test_original_rgb_and_explicit_adapter_scope(self):
        result = water.water_surface_contract(fixture())
        self.assertEqual(result['bound_source_rgb'], list(water.COLOR_AND_DENSITY[:3]))
        self.assertEqual(result['source_globals']['water_aces_color_and_density'][3], 15)
        self.assertEqual(result['renderer_coverage'], 'source_base_color_only')
        self.assertTrue(result['geometry']['preserve_original_vertex_height'])

    def test_different_map_rejected(self):
        data = fixture()
        data['source_sha256'] = '0'*64
        with self.assertRaises(ValueError):
            water.water_surface_contract(data)

    def test_changed_source_material_rejected(self):
        data = fixture()
        data['materials'][0]['texture_slots'][0] = 'other'
        with self.assertRaises(ValueError):
            water.water_surface_contract(data)

    def test_other_receiver_rejected(self):
        data = fixture()
        data['meshes'][0]['sha256'] = '0'*64
        with self.assertRaises(ValueError):
            water.water_surface_contract(data)

    def test_unrelated_materials_do_not_receive_water_adapter(self):
        data = fixture()
        data['materials'][0]['shader'] = 'simple_aces'
        self.assertIsNone(water.water_surface_contract(data))

    def test_original_level_fields_and_duplicate_guard(self):
        fields = [dict(name=name, type='point4', value=list(value)) for name, value in
                  [('water_aces_color_and_density', water.COLOR_AND_DENSITY), ('water_aces_params', water.SOURCE_PARAMS)]]
        level = dict(children=[dict(name='shader_vars', params=fields)])
        self.assertTrue(water.validate_level_globals(level))
        bad = copy.deepcopy(level)
        bad['children'][0]['params'].append(copy.deepcopy(fields[0]))
        with self.assertRaises(ValueError):
            water.validate_level_globals(bad)


if __name__ == '__main__':
    unittest.main()
