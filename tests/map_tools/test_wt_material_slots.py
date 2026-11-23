"""Regression for the two original Flanders meshes with identical material slots."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/unreal'))
from wt_material_slots import material_bindings


class MaterialSlotTests(unittest.TestCase):
    def test_native_barracks_deduplication(self):
        brick = 'WT_84c786033ddc6817292e'
        glass = 'WT_9a3c9e0714f842698e5a'
        self.assertEqual(material_bindings([brick, glass, glass], [brick, glass]), [brick, glass])

    def test_native_officer_house_deduplication(self):
        a, b, glass = 'WT_6d6bdb4288c73815d333', 'WT_5565084317f5134e0501', 'WT_9a3c9e0714f842698e5a'
        self.assertEqual(material_bindings([a, b, glass, glass], [a, b, glass]), [a, b, glass])

    def test_actual_slot_order_controls_binding(self):
        self.assertEqual(material_bindings(['wood', 'glass'], ['glass', 'wood']), ['glass', 'wood'])

    def test_distinct_material_loss_is_rejected(self):
        with self.assertRaises(ValueError):
            material_bindings(['wood', 'glass', 'glass'], ['wood'])

    def test_unknown_material_or_multiplicity_is_rejected(self):
        for actual in (['wood', 'fallback'], ['wood', 'wood', 'glass']):
            with self.subTest(actual=actual), self.assertRaises(ValueError):
                material_bindings(['wood', 'glass'], actual)


if __name__ == '__main__':
    unittest.main()
