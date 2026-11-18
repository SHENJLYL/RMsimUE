import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools' / 'unreal'))
from wt_mesh_geometry import triangle_accounting, verify_native_material_triangles


class SourceGeometryProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.package = Path(self.temp.name)
        (self.package / 'mesh.fbx').write_bytes(b'audited fixture fbx')
        self.scene_hash = 'a' * 64
        self.spec = dict(id='world_fixture', file='mesh.fbx', triangles=4, material_slots=['WT_A', 'WT_B'],
                         sha256=hashlib.sha256((self.package / 'mesh.fbx').read_bytes()).hexdigest(),
                         geometry_audit_file='proof.json')
        self.proof = dict(schema='wt_source_degenerate_face_audit_v1', status='passed',
            mesh_id=self.spec['id'], source_sha256=self.scene_hash, fbx_file=self.spec['file'],
            fbx_sha256=self.spec['sha256'], source_triangles=4, fbx_triangles=4,
            strict_coincident=1, fbx_strict_coincident=1, zero_area=2,
            zero_area_with_distinct_positions=1, expected_native_triangles=3,
            fbx_source_correspondence=dict(exact_face_multiset_equal=True),
            extra_float32_coincident=0, positive_area_collapsed_after_float32=0,
            model_transform_extra_float32_coincident=0,
            model_transform_positive_area_collapsed_after_float32=0,
            strict_coincident_source_face_indices=[1], strict_coincident_fbx_face_indices=[2],
            face_evidence=[dict(batch_face_index=1, stable_material_id='WT_A',
                                positions_dagor_m=[[0, 0, 0], [0, 0, 0], [1, 0, 0]])],
            source_material_triangle_counts={'WT_A': 3, 'WT_B': 1},
            source_material_coincident_counts={'WT_A': 1},
            source_file_count=1, source_inputs=[dict(sha256='b' * 64, triangles=4)])
        self.save_proof()

    def tearDown(self):
        self.temp.cleanup()

    def save_proof(self):
        raw = json.dumps(self.proof).encode()
        (self.package / 'proof.json').write_bytes(raw)
        self.spec['geometry_audit_sha256'] = hashlib.sha256(raw).hexdigest()

    def check(self):
        return triangle_accounting(self.package, self.spec, self.scene_hash)

    def test_original_count_without_exception(self):
        self.assertEqual(triangle_accounting(self.package, dict(triangles=4))['expected_native_triangles'], 4)

    def test_only_coincident_faces_removed_collinear_kept(self):
        result = self.check()
        self.assertEqual(result['source_triangles'], 4)
        self.assertEqual(result['expected_native_triangles'], 3)
        self.assertEqual(result['strictly_coincident_source_triangles'], 1)

    def test_proof_and_fbx_byte_tampering_rejected(self):
        (self.package / 'proof.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'proof bytes'):
            self.check()
        self.save_proof()
        (self.package / 'mesh.fbx').write_bytes(b'changed geometry')
        with self.assertRaisesRegex(ValueError, 'FBX bytes'):
            self.check()

    def test_scene_identity_and_full_correspondence_required(self):
        with self.assertRaisesRegex(ValueError, 'source_sha256'):
            triangle_accounting(self.package, self.spec, 'c' * 64)
        self.proof['fbx_source_correspondence']['exact_face_multiset_equal'] = False
        self.save_proof()
        with self.assertRaisesRegex(ValueError, 'correspondence'):
            self.check()

    def test_extra_precision_loss_is_rejected(self):
        self.proof['model_transform_extra_float32_coincident'] = 1
        self.save_proof()
        with self.assertRaisesRegex(ValueError, 'must be zero'):
            self.check()

    def test_missing_duplicate_or_positive_area_face_evidence_rejected(self):
        for faces in ([], self.proof['face_evidence'] * 2,
                      [dict(batch_face_index=1, positions_dagor_m=[[0, 0, 0], [0, 1, 0], [1, 0, 0]])]):
            with self.subTest(faces=faces):
                self.proof['face_evidence'] = faces
                self.save_proof()
                with self.assertRaises(ValueError):
                    self.check()

    def test_path_escape_rejected_before_reading_proof(self):
        self.spec['geometry_audit_file'] = str(self.package / 'proof.json')
        with self.assertRaisesRegex(ValueError, 'absolute'):
            self.check()

    def test_native_material_distribution_checked_even_when_total_matches(self):
        accounting = self.check()
        good = dict(sections=[dict(material_slot='WT_A', triangles=2), dict(material_slot='WT_B', triangles=1)])
        self.assertEqual(verify_native_material_triangles(accounting, good), {'WT_A': 2, 'WT_B': 1})
        wrong = dict(sections=[dict(material_slot='WT_A', triangles=1), dict(material_slot='WT_B', triangles=2)])
        with self.assertRaisesRegex(ValueError, 'material-section'):
            verify_native_material_triangles(accounting, wrong)


if __name__ == '__main__':
    unittest.main()
