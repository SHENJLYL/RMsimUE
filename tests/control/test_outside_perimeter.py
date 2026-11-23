"""Outside-perimeter geometry, map identity and reproducible rebuild contracts."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ros2_ws/src/rmsim_bridge"))
sys.path.insert(0, str(ROOT / "tools/integration"))
sys.path.insert(0, str(ROOT / "tools/maps"))
from rmsim_bridge.perimeter_geometry import closed_perimeter_geometry, canonical_sha256
from rmsim_bridge.navigation_map_identity import validate_localization_map, map_file_signatures
import export_boundary_navigation_package as exporter
import navigation_console_config as console


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class OutsidePerimeterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "assets/maps/rmuc2027/navigation-console"
        self.local = self.base / "localization/vertical85"; self.local.mkdir(parents=True)
        self.boundary = self.base.parent / "arena-shell.json"
        self.output = self.base.parent / "navigation-console-outside"
        self.scene = {"id": "rmuc2027", "units": "m", "coordinate_system": "ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP",
                      "bounds_m": {"lo": [-1., -1., -.3], "hi": [1., 1., 1.]},
                      "spawns": [{"id": "default", "position_m": [0., 0., 0.], "yaw_rad": 0.}]}
        self.manifest = {"map_id": "rmuc2027", "units": "m", "coordinate_system": self.scene["coordinate_system"],
                         "bounds_m": copy.deepcopy(self.scene["bounds_m"]), "venue_enabled": False,
                         "navigation_package": "navigation-console-outside",
                         "perimeter": {"physics_contract": "outside_source_bounds_v1", "mode": "continuous_closed",
                                       "doors": "fixed_closed_decorative", "interaction": False,
                                       "outside_clearance_m": .001, "collision_thickness_m": .48,
                                       "collision_bottom_m": -1.25, "collision_top_m": 4.8}}
        exporter.write_json(self.boundary, self.manifest)
        exporter.write_json(self.base / "scene.json", self.scene)
        descriptor = self.base.parent / "mesh-source.json"
        exporter.write_json(descriptor, {"source_to_sim_translation_m": [0., 0., 0.]})
        pixels = np.full((4, 4), 254, dtype=np.uint8); pixels[1, 2] = 0
        for folder in (self.base, self.local):
            exporter.write_pgm(folder / "map.pgm", pixels)
            (folder / "map.yaml").write_text("image: map.pgm\nresolution: 0.5\norigin: [-1.0, -1.0, 0.0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n", encoding="utf-8")
        np.savez_compressed(self.local / "surface-provenance.npz", row=np.array([1], dtype="<i4"),
            col=np.array([2], dtype="<i4"), group_id=np.array([0], dtype="<i4"),
            contributing_triangles=np.array([1], dtype="<u4"), z_min_m=np.array([0.]), z_max_m=np.array([1.]),
            abs_normal_z_min=np.array([0.]), abs_normal_z_max=np.array([0.]))
        scene_sha = console.scene_geometry(self.scene)
        meta = {"role": "offline_geometry_candidate", "status": "fixture", "map_id": "rmuc2027",
                "scene_geometry_sha256": scene_sha, "descriptor_sha256": digest(descriptor),
                "source_collision_sha256": "3"*64, "source_to_sim_translation_m": [0., 0., 0.],
                "coordinate_system": self.scene["coordinate_system"], "units": "m",
                "width": 4, "height": 4, "resolution": .5, "origin": [-1., -1., 0.],
                "map_yaml_sha256": digest(self.local / "map.yaml"), "raster_sha256": digest(self.local / "map.pgm"),
                "surface_provenance_sha256": digest(self.local / "surface-provenance.npz"),
                "reference_navigation_raster_sha256": digest(self.base / "map.pgm"),
                "groups": ["fixture_CAD"], "selected_triangles": 1, "selected_triangles_by_group": {"fixture_CAD": 1},
                "exporter_sha256": "4"*64}
        exporter.write_json(self.local / "metadata.json", meta)
        files = {role: {"path": "localization/vertical85/"+name, "sha256": digest(self.local / name)}
                 for role, name in (("yaml", "map.yaml"), ("raster", "map.pgm"), ("metadata", "metadata.json"),
                                    ("surface_provenance", "surface-provenance.npz"))}
        spec = {"scene_geometry_sha256": scene_sha, "raster_sha256": digest(self.base / "map.pgm"),
                "map_metadata": {"map_id": "rmuc2027", "source_md5": "1"*32, "collision_sha256": "3"*64,
                                 "width": 4, "height": 4, "resolution": .5, "origin": [-1., -1., 0.]},
                "localization_map": {"schema": "rmsim_packaged_localization_map_v1", "default": True,
                                     "files": files, "export_recipe": {"schema": "rmsim_localization_surface_export_recipe_v1"}}}
        exporter.write_json(self.base / "SOURCE.json", spec)
        for relative in ("tools/maps/export_boundary_navigation_package.py", "tools/maps/export_localization_surface_map.py",
                         "ros2_ws/src/rmsim_bridge/rmsim_bridge/perimeter_geometry.py",
                         "ros2_ws/src/rmsim_bridge/rmsim_bridge/navigation_map_identity.py"):
            target = self.root / relative; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)

    def generate(self):
        return exporter.generate(self.base, self.boundary, self.output, self.root)

    def prepared(self, name="run"):
        with patch.object(console, "ROOT", self.root):
            return console.prepare_scene("rmuc2027", self.root / name)

    def test_collision_slabs_outside_and_all_corner_squares_continuous(self):
        geometry = closed_perimeter_geometry(self.manifest, self.scene["bounds_m"])
        walls = {wall["id"]: wall for wall in geometry["walls"]}
        for wall in walls.values():
            overlap = [min(wall["hi"][i], 1.)-max(wall["lo"][i], -1.) for i in (0, 1)]
            self.assertLessEqual(min(overlap), -.000999999)
        for xwall in ("west", "east"):
            for ywall in ("north", "south"):
                a, b = walls[xwall], walls[ywall]
                for axis in (0, 1):
                    self.assertAlmostEqual(min(a["hi"][axis], b["hi"][axis])-max(a["lo"][axis], b["lo"][axis]), .48)

    def test_rejects_inward_and_changed_source_bounds(self):
        for value in (-.001, float("nan"), True):
            bad = copy.deepcopy(self.manifest); bad["perimeter"]["outside_clearance_m"] = value
            with self.assertRaises(ValueError): closed_perimeter_geometry(bad)
        bounds = copy.deepcopy(self.scene["bounds_m"]); bounds["hi"][0] += .001
        with self.assertRaisesRegex(ValueError, "source competition"):
            closed_perimeter_geometry(self.manifest, bounds)

    def test_art_changes_leave_physics_identity_unchanged(self):
        old = canonical_sha256(closed_perimeter_geometry(self.manifest))
        self.manifest.update(venue_enabled=True, layout="other/model.json", lighting={"sun": 123})
        self.assertEqual(old, canonical_sha256(closed_perimeter_geometry(self.manifest)))

    def test_padding_preserves_cad_pixels_and_world_coordinates(self):
        before = {p: digest(p) for p in self.base.rglob("*") if p.is_file()}
        report = self.generate(); grid = report["grid"]; pad = grid["padding_cells"]
        for relative in ("map.pgm", "localization/vertical85/map.pgm"):
            a, b = exporter.read_pgm(self.base / relative), exporter.read_pgm(self.output / relative)
            np.testing.assert_array_equal(a, b[pad:pad+4, pad:pad+4])
        self.assertEqual(grid["origin"][0]+pad*.5, -1.)
        self.assertEqual(grid["origin"][1]+pad*.5, -1.)
        self.assertEqual(before, {p: digest(p) for p in before})
        self.assertFalse(report["navigation_parameters_modified"])
        self.assertFalse(report["evaluation_poses_used"])

    def test_actual_wall_faces_are_occupied_in_both_new_maps(self):
        report = self.generate(); grid = report["grid"]
        nav = exporter.read_pgm(self.output / "map.pgm")
        local = exporter.read_pgm(self.output / "localization/vertical85/map.pgm")
        for side in (-1, 1):
            for x, y in ((side*1.001, 0.), (0., side*1.001)):
                col = int((x-grid["origin"][0])/grid["resolution"])
                row = int((y-grid["origin"][1])/grid["resolution"])
                self.assertEqual(nav[row, col], 0); self.assertEqual(local[row, col], 0)
        with np.load(self.output / "localization/vertical85/surface-provenance.npz") as provenance:
            for group in range(1, 5): self.assertTrue(np.any(provenance["group_id"] == group))

    def test_startup_and_reload_bind_actual_boundary_and_accept_art_edits(self):
        self.generate(); prepared = self.prepared()
        identity = validate_localization_map(prepared["map_yaml"], prepared["localization_map_yaml"])
        self.assertIn(str(self.boundary.resolve()), map_file_signatures(identity))
        self.manifest["venue_enabled"] = True; exporter.write_json(self.boundary, self.manifest)
        self.assertEqual(identity, validate_localization_map(prepared["map_yaml"], prepared["localization_map_yaml"]))
        self.manifest["perimeter"]["outside_clearance_m"] = .002
        exporter.write_json(self.boundary, self.manifest)
        with self.assertRaisesRegex(ValueError, "Physical perimeter changed"):
            validate_localization_map(prepared["map_yaml"], prepared["localization_map_yaml"])
        with self.assertRaisesRegex(ValueError, "active physical perimeter"):
            self.prepared("stale")

    def test_old_localization_map_cannot_be_selected_for_new_boundary(self):
        self.generate(); prepared = self.prepared()
        with self.assertRaises(ValueError):
            validate_localization_map(prepared["map_yaml"], self.local / "map.yaml")

    def test_missing_baseline_and_binary_restore_exact_and_corrupt_existing_rejected(self):
        self.generate()
        files = ["map.pgm", "localization/vertical85/map.pgm", "localization/vertical85/surface-provenance.npz"]
        before = {p: digest(p) for p in self.output.rglob("*") if p.is_file()}
        baseline_hash = digest(self.base / "map.pgm")
        (self.base / "map.pgm").unlink()
        for name in files: (self.output / name).unlink()
        command = [sys.executable, str(self.root / "tools/maps/export_boundary_navigation_package.py"),
                   "--restore-package", str(self.output / "SOURCE.json")]
        good = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(digest(self.base / "map.pgm"), baseline_hash)
        self.assertEqual(before, {p: digest(p) for p in before})
        (self.output / "map.pgm").write_bytes(b"corrupt existing evidence")
        bad = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("Existing generated boundary file differs", bad.stderr)
        self.assertEqual((self.output / "map.pgm").read_bytes(), b"corrupt existing evidence")


if __name__ == "__main__":
    unittest.main()
