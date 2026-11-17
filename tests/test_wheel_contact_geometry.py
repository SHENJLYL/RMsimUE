"""Source geometry changes must invalidate cylinder caches; reject unsafe inputs."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools/assets'))
from wheel_contact_geometry import geometry_hash, source_vertices


class WheelContactGeometryTests(unittest.TestCase):
    def test_geometry_identity_and_validation(self):
        vertices=[[0,0,0],[.04,0,0],[0,.02,0],[0,0,.04]]
        self.assertEqual(geometry_hash(None),'')  # Existing cylindrical packages remain readable.
        self.assertNotEqual(geometry_hash(vertices),'')
        self.assertEqual(geometry_hash(vertices),geometry_hash(source_vertices(vertices)))
        modified=copy.deepcopy(vertices);modified[1][0]+=.001
        self.assertNotEqual(geometry_hash(vertices),geometry_hash(modified))
        for invalid in [[],vertices[:3],vertices*257,[[0,0,0]]*4,
                        [[float('nan'),0,0]]+vertices[1:],[[2,0,0]]+vertices[1:],
                        [[True,0,0]]+vertices[1:],[[0,0]]+vertices[1:]]:
            with self.subTest(invalid=repr(invalid)[:80]), self.assertRaises(ValueError):
                source_vertices(invalid)


if __name__=='__main__':
    unittest.main()
