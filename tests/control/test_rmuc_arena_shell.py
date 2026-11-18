"""RMUC perimeter and open-source CC0 spectator shell contract."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]
PACK=ROOT/'assets/maps/rmuc2027'
SHELL=PACK/'arena-shell.json'

class RmucArenaShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config=json.loads(SHELL.read_text(encoding='utf-8-sig'))
        cls.map=json.loads((PACK/'map.json').read_text(encoding='utf-8-sig'))
    def test_map_stays_source_derived(self):
        c=self.config
        self.assertEqual(c['schema'],'rmsim_arena_shell_v1')
        self.assertEqual(c['map_id'],self.map['id'])
        self.assertEqual(c['bounds_m'],self.map['bounds_m'])
        self.assertEqual(c['verified_source_map_semantic_sha256'],
                         hashlib.sha256(json.dumps(self.map,sort_keys=True,
                                     separators=(',',':'),ensure_ascii=False).encode('utf-8')).hexdigest())
    def test_four_full_side_barriers_close_corners(self):
        box=self.config['bounds_m']
        lo,hi=box['lo'],box['hi']
        cx=(lo[0]+hi[0])/2;cy=(lo[1]+hi[1])/2
        hx=(hi[0]-lo[0])/2;hy=(hi[1]-lo[1])/2
        b=self.config['perimeter']
        inset,thickness=b['inset_m'],b['wall_thickness_m']
        self.assertGreater(inset,thickness/2)
        self.assertLess(inset+thickness/2,.75)
        self.assertGreaterEqual(b['collision_top_m'],hi[2]+.6)
        self.assertLess(b['collision_bottom_m'],lo[2])
        self.assertLess(b['facade_top_m'],b['collision_top_m'])
        walls=[
            ((cx-hx+inset,cy),(thickness,2*hy)),
            ((cx+hx-inset,cy),(thickness,2*hy)),
            ((cx,cy-hy+inset),(2*hx,thickness)),
            ((cx,cy+hy-inset),(2*hx,thickness))]
        def contained(x,y):
            return any(abs(x-c[0])<=dims[0]/2 and abs(y-c[1])<=dims[1]/2 for c,dims in walls)
        for iy in range(201):
            y=cy-hy+2*hy*iy/200
            for x in (cx-hx+inset,cx+hx-inset):
                self.assertTrue(contained(x,y),(x,y))
        for ix in range(301):
            x=cx-hx+2*hx*ix/300
            for y in (cy-hy+inset,cy+hy-inset):
                self.assertTrue(contained(x,y),(x,y))
        self.assertGreater(abs(walls[1][0][0]-walls[0][0][0]),26)
        self.assertGreater(abs(walls[3][0][1]-walls[2][0][1]),14)
    def test_reused_cc0_source_has_immutable_byte_hashes(self):
        src=PACK/'arena/kenney-mini-arena'
        data=json.loads((src/'SOURCE.json').read_text(encoding='utf-8'))
        self.assertEqual(data['license'],'CC0-1.0')
        license_text=(src/'LICENSE.txt').read_text(encoding='utf-8-sig',errors='replace')
        self.assertIn('License: (Creative Commons Zero, CC0)',license_text)
        for name,sha in data['selected_files'].items():
            self.assertEqual(hashlib.sha256((src/name).read_bytes()).hexdigest(),sha)
        self.assertEqual(len([n for n in data['selected_files'] if n.endswith('.fbx')]),6)
        for key,path in self.config['models'].items():
            self.assertIn(key+'.fbx',data['selected_files'])
            self.assertTrue(path.startswith('/Game/RMSim/Maps/rmuc2027/Arena/Kenney/'))
    def test_native_matte_palette_material_assets(self):
        directory=ROOT/'unreal/RMSim/Content/RMSim/Maps/rmuc2027/Arena/Materials'
        keys=('Mat_Facade','Mat_Seats','Mat_UpperSeats','Mat_Supports',
              'Mat_Roof','Mat_AccentRed','Mat_AccentBlue','Mat_AccentLight')
        self.assertEqual(sorted(p.stem for p in directory.glob('*.uasset')),sorted(keys))
        self.assertLess(sum((directory/(k+'.uasset')).stat().st_size for k in keys),150000)
        c=(ROOT/'unreal/RMSim/Source/RMSim/ArenaShell.cpp').read_text(encoding='utf-8')
        for key in keys:self.assertIn('TEXT("'+key+'")',c)
        self.assertEqual(c.count(',Palette[TEXT('),15)
        self.assertIn('Component->SetMaterial(Index,Material)',c)

    def test_spectator_only_optimizations(self):
        style=self.config['stadium']
        self.assertFalse(style['dynamic_shadows'])
        self.assertFalse(style['collision'])
        self.assertFalse(style['long_distance_ornament'])
        self.assertEqual(style['tiers'],4)
        self.assertGreater(style['canopy_height_m'],self.config['perimeter']['collision_top_m']+3.)
        self.assertGreater(style['minimum_detail_distance_m'],10.)
    def test_physical_barriers_and_visual_meshes_separate(self):
        cpp=(ROOT/'unreal/RMSim/Source/RMSim/ArenaShell.cpp').read_text(encoding='utf-8')
        self.assertIn('Actor->SetActorHiddenInGame(true)',cpp)
        self.assertIn('Comp->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics)',cpp)
        self.assertIn('Component->SetCollisionEnabled(ECollisionEnabled::NoCollision)',cpp)
        self.assertIn('UInstancedStaticMeshComponent',cpp)
        self.assertIn('Component->SetCastShadow(false)',cpp)
        self.assertIn('Component->SetCanEverAffectNavigation(false)',cpp)

if __name__=='__main__':
    unittest.main()
