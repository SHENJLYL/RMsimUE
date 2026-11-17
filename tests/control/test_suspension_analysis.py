"""The settling metric must include the full tail, not just first band entry."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/integration'))
from analyze_suspension import settling_time


def rows(values):
    return [{'phase_time_s': i / 10, 'chassis_z_cm': value} for i, value in enumerate(values)]


class SuspensionAnalysisTests(unittest.TestCase):
    def test_already_settled(self):
        self.assertEqual(settling_time(rows([0.0] * 11), 0.0, .05), 0.0)

    def test_later_excursion_delays_settling(self):
        self.assertAlmostEqual(settling_time(rows([.2, 0, .1] + [0] * 9), 0.0, .05), .3)

    def test_no_settling(self):
        self.assertIsNone(settling_time(rows([.2] * 11), 0.0, .05))

    def test_short_confirmation_is_rejected(self):
        self.assertIsNone(settling_time(rows([.2] * 8 + [0] * 3), 0.0, .05))

    def test_invalid_reference_and_band(self):
        for reference, band in [(float('nan'), .05), (0, 0), (0, float('inf'))]:
            with self.subTest(reference=reference, band=band), self.assertRaises(ValueError):
                settling_time(rows([0] * 11), reference, band)

    def test_empty_trace_is_rejected(self):
        with self.assertRaises(ValueError):
            settling_time([], 0.0, .05)


if __name__ == '__main__':
    unittest.main()
