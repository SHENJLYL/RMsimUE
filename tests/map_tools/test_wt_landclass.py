"""Fail-closed checks for the external global landclass source binding."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tools/unreal'))
from wt_landclass import (SCHEMA, PACKAGE_SCHEMA, PROOF_SCHEMA, DAGOR_COMMIT,
                         LAND_MATERIAL_ID, CLASS_NAMES, COLOR_NAMES, DATA_NAMES,
                         digest, shader_vectors, validate_landclass_surface)


def png(width, height):
    def chunk(kind, value):
        return struct.pack('>I', len(value))+kind+value+struct.pack('>I', zlib.crc32(kind+value)&0xffffffff)
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('>II5B', width, height, 8, 2, 0, 0, 0))+
            chunk(b'IDAT', zlib.compress((b'\0'+bytes(width*3))*height))+chunk(b'IEND', b''))


class LandclassContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.package = Path(self.tmp.name)
        (self.package/'landclass').mkdir()
        textures = {}
        for i, name in enumerate([*COLOR_NAMES, *DATA_NAMES]):
            parameter = 'LC'+str(i) if i<5 else name
            size = [1, 1] if i<5 else [7616, 272] if i==5 else [32, 32]
            path = self.package/'landclass'/(name+'.png')
            path.write_bytes(png(*size))
            textures[parameter] = dict(name=name, file='landclass/'+path.name, sha256=digest(path), size=size,
                srgb=i<5, nearest=i>5, no_mips=i>=5, address='wrap' if i<5 else 'clamp')
        cells = [dict(cell=i, xy=[i%32, i//32], class_ids=[0]+[255]*6,
                      blend_count=1, pages=[0, 0]) for i in range(1024)]
        self.data = dict(schema=PACKAGE_SCHEMA, source_sha256='a'*64, source_lmap_sha256='b'*64,
            dagor_commit=DAGOR_COMMIT, domain=dict(cell_m=1024., grid_m=16., cells=[32, 32], origin_cells=[-16, -16]),
            atlas=dict(element_size=64, page_size=68, pages_per_row=112, page_count=448, dimensions=[7616, 272],
                       ddsx_sha256='c'*64, decoded_rgb_sha256='d'*64),
            classes=[dict(id=i, name=name, texture_name=COLOR_NAMES[i], size_m=[4096,2048,2048,1024,128][i],
                          offset_m=[0.,0.], source_record_sha256='e'*64) for i,name in enumerate(CLASS_NAMES)],
            cells=cells, textures=textures, verification_file='landclass/verification.json')
        proof = {key:self.data[key] for key in ('source_sha256','source_lmap_sha256','domain','atlas','classes','textures')}
        proof.update(schema=PROOF_SCHEMA, status='passed',
            checks=dict(source_boundaries_exact=True, packed_records_roundtrip_exact=True,
                        atlas_decoded_pixels_exact=True, weights_source_formula_checked=True),
            cells_sha256=hashlib.sha256(json.dumps(cells,sort_keys=True,separators=(',',':')).encode()).hexdigest())
        path = self.package/'landclass/verification.json'
        path.write_text(json.dumps(proof))
        self.data['verification_sha256'] = digest(path)
        self.source = dict(source_sha256='a'*64,
            meshes=[dict(id='world_land_000', material_slots=[LAND_MATERIAL_ID])],
            source_landclass_surface=dict(schema=SCHEMA, shader='land_mesh', material_id=LAND_MATERIAL_ID,
                mesh_ids=['world_land_000'], manifest_file='landclass/landclass.json'))
        self.write_manifest()

    def write_manifest(self):
        path = self.package/'landclass/landclass.json'
        path.write_text(json.dumps(self.data))
        self.source['source_landclass_surface']['manifest_sha256'] = digest(path)

    def test_valid_contract_and_original_negative_v(self):
        data = validate_landclass_surface(self.package, self.source)
        self.assertEqual(shader_vectors(data)['LCMap1'], (1/2048, -1/2048, 8, -8))

    def test_rejects_missing_global_binding(self):
        del self.source['source_landclass_surface']
        with self.assertRaisesRegex(ValueError, 'missing its global'):
            validate_landclass_surface(self.package, self.source)

    def test_rejects_original_level_mismatch(self):
        self.source['source_sha256'] = 'f'*64
        with self.assertRaisesRegex(ValueError, 'original level'):
            validate_landclass_surface(self.package, self.source)

    def test_rejects_changed_texture_bytes(self):
        path = self.package/self.data['textures']['WT_LandWeights']['file']
        with path.open('ab') as handle:
            handle.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'texture bytes'):
            validate_landclass_surface(self.package, self.source)

    def test_rejects_rehashed_cell_record_without_original_proof(self):
        self.data['cells'][0]['class_ids'][0] = 1
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'cell proof'):
            validate_landclass_surface(self.package, self.source)

    def test_rejects_path_escape(self):
        self.source['source_landclass_surface']['manifest_file'] = '../landclass.json'
        with self.assertRaisesRegex(ValueError, 'nonlocal'):
            validate_landclass_surface(self.package, self.source)


if __name__ == '__main__':
    unittest.main()
