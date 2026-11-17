import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rm_map_tools.map import MapError, load_map, blocked, occupancy
from rm_map_tools.__main__ import main


class MapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'map.json'
        self.data = json.loads((ROOT / 'assets/maps/import_lab/map.json').read_text(encoding='utf-8'))

    def write(self):
        self.path.write_text(json.dumps(self.data), encoding='utf-8')
        return self.path

    def test_two_maps_have_different_geometry_and_spawns(self):
        a, ha = load_map(ROOT / 'assets/maps/import_lab')
        b, hb = load_map(ROOT / 'assets/maps/warehouse_lanes')
        self.assertNotEqual(ha, hb)
        self.assertNotEqual(a['floor']['size_m'], b['floor']['size_m'])
        self.assertNotEqual(a['spawns'][0], b['spawns'][0])
        self.assertEqual(len(b['obstacles']), 5)

    def test_raster_orientation_and_declared_boxes(self):
        data, _ = load_map(self.write())
        width, height, origin, pixels = occupancy(data, .05)
        def cell(x,y):
            col = int((x-origin[0])/.05)
            row = height - 1 - int((y-origin[1])/.05)
            return pixels[row*width+col]
        self.assertEqual((width,height), (400,400))
        self.assertEqual(cell(3.5,1), 0) # ROS +Y; wrong top/bottom raster would miss this box.
        self.assertEqual(cell(3.5,-1), 254)
        self.assertEqual(cell(0,0), 254)
        self.assertEqual(cell(9.95,0), 0)

    def test_yawed_box_is_not_just_its_aabb(self):
        self.data['obstacles'][0].update(center_m=[3,2,.6],size_m=[2,.2,1.2],yaw_rad=math.pi/4)
        data, _ = load_map(self.write())
        self.assertTrue(blocked(data,3.5,2.5))
        self.assertFalse(blocked(data,3.5,1.5))

    def test_spawn_inside_box_or_wall_is_rejected(self):
        for point in ([3.5,1,0],[9.95,0,0]):
            with self.subTest(point=point):
                self.data['spawns'][0]['position_m']=point
                with self.assertRaisesRegex(MapError,'blocked'):
                    load_map(self.write())

    def test_invalid_dimensions_and_outside_geometry(self):
        baseline = copy.deepcopy(self.data)
        for mutation in (lambda d:d['floor'].update(size_m=[0,20]),
                         lambda d:d['obstacles'][0].update(center_m=[9.7,1,.6]),
                         lambda d:d['obstacles'][0].update(center_m=[3,1,5]),
                         lambda d:d['regions'][0].update(center_m=[20,0])):
            self.data=copy.deepcopy(baseline);mutation(self.data)
            with self.assertRaises(MapError):load_map(self.write())

    def test_unknown_duplicate_and_nonfinite_fields(self):
        baseline=copy.deepcopy(self.data)
        for mutation in (lambda d:d.update(extra=1),
                         lambda d:d.update(schema_version=True),
                         lambda d:d['spawns'][0].update(yaw_rad=float('nan')),
                         lambda d:d['regions'].append(copy.deepcopy(d['regions'][0])),
                         lambda d:d.update(inspection_obstacle_id='missing')):
            self.data=copy.deepcopy(baseline);mutation(self.data)
            with self.assertRaises(MapError):load_map(self.write())
        self.path.write_text('{"id":"a","id":"b"}')
        with self.assertRaisesRegex(MapError,'duplicate JSON'):load_map(self.path)

    def test_export_is_reproducible_and_does_not_overwrite(self):
        p=self.write();out=Path(self.temp.name)/'export'
        self.assertEqual(main(['export',str(p),'--output',str(out)]),0)
        image=(out/'map.pgm').read_bytes()
        self.assertTrue(image.startswith(b'P5\n400 400\n255\n'))
        self.assertEqual(main(['export',str(p),'--output',str(out)]),2)
        self.assertEqual((out/'map.pgm').read_bytes(),image)
        out2=Path(self.temp.name)/'export2'
        self.assertEqual(main(['export',str(p),'--output',str(out2)]),0)
        self.assertEqual((out2/'map.pgm').read_bytes(),image)
        self.assertIn('origin: [-10.0, -10.0, 0]',(out/'map.yaml').read_text())

    def test_bounded_grid(self):
        self.data['floor']['size_m']=[100,100]
        data,_=load_map(self.write())
        with self.assertRaisesRegex(MapError,'4 million'):occupancy(data,.01)
        with self.assertRaises(MapError):occupancy(data,float('nan'))

    def test_maps_need_not_contain_a_test_obstacle(self):
        self.data['obstacles']=[]
        self.data.pop('inspection_obstacle_id')
        data,_=load_map(self.write())
        self.assertFalse(blocked(data,0,0))
        self.assertTrue(blocked(data,9.95,0))


if __name__=='__main__':unittest.main()
