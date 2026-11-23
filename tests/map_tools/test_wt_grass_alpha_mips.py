"""Binary-regression tests for imported full-chain grass alpha Source DDS."""
import importlib.util
from pathlib import Path
import struct
import unittest

SOURCE = Path(__file__).resolve().parents[2]/'tools/unreal/wt_grass_alpha_mips.py'
if not SOURCE.is_file():
    SOURCE = Path(__file__).with_name('wt_grass_alpha_mips.py')
spec = importlib.util.spec_from_file_location('grass_alpha_mips_under_test', SOURCE)
alpha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alpha)


class AlphaSourceDdsTests(unittest.TestCase):
    def setUp(self):
        self.mips = [bytes(range(32)), bytes(range(8)), b'\x80\xff', b'\x7f']
        self.data = alpha.write_r8_dds(8, 4, self.mips)

    def test_all_pixels_and_nonsquare_tail_survive(self):
        decoded = alpha.parse_r8_dds(self.data)
        self.assertEqual([m['size'] for m in decoded['mips']], [[8,4],[4,2],[2,1],[1,1]])
        self.assertEqual([m['pixels'] for m in decoded['mips']], self.mips)

    def test_equivalent_ue_legacy_l8_export_is_accepted(self):
        header = bytearray(self.data[:128])
        struct.pack_into('<8I',header,76,32,0x20000,0,8,255,0,0,0)
        decoded = alpha.parse_r8_dds(bytes(header)+self.data[148:])
        self.assertEqual([m['pixels'] for m in decoded['mips']], self.mips)

    def test_source_level_loss_is_rejected(self):
        bad = bytearray(self.data)
        struct.pack_into('<I',bad,28,1)
        with self.assertRaisesRegex(ValueError,'every original mip'):
            alpha.parse_r8_dds(bad)

    def test_truncated_and_trailing_payload_are_rejected(self):
        for bad in (self.data[:-1],self.data+b'\0'):
            with self.assertRaises(ValueError):
                alpha.parse_r8_dds(bad)

    def test_bc4_and_wrong_channel_formats_are_rejected(self):
        for dxgi in (80,28,62):
            bad = bytearray(self.data)
            struct.pack_into('<I',bad,128,dxgi)
            with self.assertRaisesRegex(ValueError,'R8_UNORM'):
                alpha.parse_r8_dds(bad)

    def test_arrays_and_cubes_are_rejected(self):
        for position,value in ((136,4),(140,2)):
            bad = bytearray(self.data)
            struct.pack_into('<I',bad,position,value)
            with self.assertRaises(ValueError):
                alpha.parse_r8_dds(bad)

    def test_padded_rows_and_non_power_two_are_rejected(self):
        for position,value in ((20,16),(16,7)):
            bad = bytearray(self.data)
            struct.pack_into('<I',bad,position,value)
            with self.assertRaises(ValueError):
                alpha.parse_r8_dds(bad)


if __name__ == '__main__':
    unittest.main()
