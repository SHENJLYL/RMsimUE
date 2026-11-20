"""Platform perimeter keeps explicit anchors, source provenance and navigation identities."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_outside_perimeter as fixtures


class PlatformPerimeterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.OutsidePerimeterTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture
        self.f.manifest["perimeter"].update(
            physics_contract="outside_platform_bounds_v1",
            outside_clearance_m=0.,
            target_bounds_m={"lo": [-1., -.62, -.3], "hi": [1., .71, 1.]})
        fixtures.exporter.write_json(self.f.boundary, self.f.manifest)

    def test_asymmetric_platform_faces_and_corners_preserve_source_x_and_z(self):
        f = self.f
        g = fixtures.closed_perimeter_geometry(f.manifest, f.scene["bounds_m"])
        self.assertEqual(g["source_bounds_m"], f.scene["bounds_m"])
        self.assertEqual(g["target_bounds_m"], f.manifest["perimeter"]["target_bounds_m"])
        walls = {w["id"]: w for w in g["walls"]}
        self.assertEqual(walls["west"]["hi"][0], -1.)
        self.assertEqual(walls["east"]["lo"][0], 1.)
        self.assertEqual(walls["south"]["hi"][1], -.62)
        self.assertEqual(walls["north"]["lo"][1], .71)
        self.assertGreater(walls["south"]["hi"][1], g["source_bounds_m"]["lo"][1])
        self.assertLess(walls["north"]["lo"][1], g["source_bounds_m"]["hi"][1])
        for xside in ("west", "east"):
            for yside in ("south", "north"):
                for axis in (0, 1):
                    a, b = walls[xside], walls[yside]
                    self.assertAlmostEqual(min(a["hi"][axis], b["hi"][axis]) -
                                           max(a["lo"][axis], b["lo"][axis]), .48)

    def test_bad_target_cannot_move_short_ends_or_source_vertical_bounds(self):
        f = self.f
        variants = []
        missing = copy.deepcopy(f.manifest); del missing["perimeter"]["target_bounds_m"]
        variants.append(missing)
        for side, axis, value in (("lo", 0, -.9), ("hi", 0, 1.1),
                                  ("lo", 2, -.2), ("hi", 2, .9),
                                  ("lo", 1, -1.01), ("hi", 1, 1.01),
                                  ("lo", 1, float("nan")), ("hi", 1, True),
                                  ("hi", 1, -.7)):
            bad = copy.deepcopy(f.manifest)
            bad["perimeter"]["target_bounds_m"][side][axis] = value
            variants.append(bad)
        for index, bad in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(ValueError):
                fixtures.closed_perimeter_geometry(bad, f.scene["bounds_m"])

    def test_both_rasters_and_provenance_use_the_real_platform_inner_faces(self):
        f = self.f
        before = {p: fixtures.digest(p) for p in f.base.rglob("*") if p.is_file()}
        report = f.generate(); grid = report["grid"]
        nav = fixtures.exporter.read_pgm(f.output / "map.pgm")
        local = fixtures.exporter.read_pgm(f.output / "localization/vertical85/map.pgm")
        g = fixtures.closed_perimeter_geometry(f.manifest)
        for face in g["localization_faces"]:
            point = [(a+b)*.5 for a, b in zip(face["start_xy_m"], face["end_xy_m"])]
            col = int((point[0]-grid["origin"][0])/grid["resolution"])
            row = int((point[1]-grid["origin"][1])/grid["resolution"])
            self.assertEqual(nav[row, col], 0, face["id"])
            self.assertEqual(local[row, col], 0, face["id"])
        pad = grid["padding_cells"]
        self.assertEqual(grid["origin"][0]+pad*.5, -1.)
        self.assertEqual(grid["origin"][1]+pad*.5, -1.)
        with fixtures.np.load(f.output / "localization/vertical85/surface-provenance.npz") as data:
            cad = data["group_id"] == 0
            fixtures.np.testing.assert_array_equal(data["row"][cad], [1+pad])
            fixtures.np.testing.assert_array_equal(data["col"][cad], [2+pad])
            for group in range(1, 5):
                self.assertTrue(fixtures.np.any(data["group_id"] == group))
        self.assertEqual(before, {p: fixtures.digest(p) for p in before})
        self.assertTrue(report["cad_grid_world_positions_preserved"])
        self.assertFalse(report["navigation_parameters_modified"])

    def test_console_and_map_reload_bind_target_separately_from_source(self):
        f = self.f
        f.generate(); prepared = f.prepared()
        identity = fixtures.validate_localization_map(prepared["map_yaml"],
                                                     prepared["localization_map_yaml"])
        self.assertIn(str(f.boundary.resolve()), fixtures.map_file_signatures(identity))
        source = json.loads((f.output / "SOURCE.json").read_text())
        g = source["runtime_boundary"]["geometry"]
        self.assertEqual(g["physics_contract"], "outside_platform_bounds_v1")
        self.assertEqual(g["source_bounds_m"], f.scene["bounds_m"])
        self.assertEqual(g["target_bounds_m"], f.manifest["perimeter"]["target_bounds_m"])
        f.manifest["perimeter"]["target_bounds_m"]["hi"][1] -= .01
        fixtures.exporter.write_json(f.boundary, f.manifest)
        with self.assertRaisesRegex(ValueError, "Physical perimeter changed"):
            fixtures.validate_localization_map(prepared["map_yaml"], prepared["localization_map_yaml"])
        with self.assertRaisesRegex(ValueError, "active physical perimeter"):
            f.prepared("stale-platform")

    def test_console_rejects_legacy_package_without_runtime_boundary(self):
        f = self.f
        f.manifest["navigation_package"] = "navigation-console"
        fixtures.exporter.write_json(f.boundary, f.manifest)
        with self.assertRaisesRegex(ValueError, "active physical perimeter"):
            f.prepared("missing-runtime-boundary")

    def test_platform_generated_files_restore_exactly(self):
        f = self.f
        f.generate()
        before = {p: fixtures.digest(p) for p in f.output.rglob("*") if p.is_file()}
        for name in ("map.pgm", "localization/vertical85/map.pgm",
                     "localization/vertical85/surface-provenance.npz"):
            (f.output / name).unlink()
        result = subprocess.run([sys.executable,
            str(f.root / "tools/maps/export_boundary_navigation_package.py"),
            "--restore-package", str(f.output / "SOURCE.json")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, {p: fixtures.digest(p) for p in before})


if __name__ == "__main__":
    unittest.main()
