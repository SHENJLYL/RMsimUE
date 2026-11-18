"""VROMFS/BLK byte fixtures; extraction uses CPython 3.14 compression.zstd.

Older Python discovery explicitly skips these dependency-bound cases.
"""
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

try:
    from compression import zstd
except ImportError:
    zstd = None

ZSTD_REQUIRED = 'WT binary extraction requires CPython 3.14+ compression.zstd'
if zstd is not None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools' / 'maps'))
    from wt_blk import Reader, decode_blk, shared_context
    from wt_vromfs import bounded_zstd, read_vromfs


def vu(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def names(values):
    if not values:
        return b'\0'
    data = b'\0'.join(values) + b'\0'
    return vu(len(values)) + vu(len(data)) + data


def blk(parameter_records, complex_bytes=b'', name_bytes=(b'position', b'color'), block=None):
    table = b''.join(parameter_records)
    count = len(parameter_records)
    body = names(name_bytes) + vu(1) + vu(count) + vu(len(complex_bytes)) + complex_bytes + table
    return b'\1' + body + (b'\0' + vu(count) + b'\0' if block is None else block)


def param(name_id, kind, value):
    return struct.pack('<II', name_id | (kind << 24), value)


def vrom_body(items):
    count = len(items)
    entry_start = 32 + count * 8
    body = bytearray(entry_start + count * 16)
    struct.pack_into('<IIQIIQ', body, 0, 32, count, 0, entry_start, count, 0)
    for index, (name, content) in enumerate(items):
        name_start = len(body)
        body.extend(name + b'\0')
        content_start = len(body)
        body.extend(content)
        struct.pack_into('<Q', body, 32 + index * 8, name_start)
        struct.pack_into('<IIQ', body, entry_start + index * 16, content_start, len(content), 0)
    return bytes(body)


def vrom(body, codec=None, declared_size=None):
    if codec == 'zlib':
        payload = zlib.compress(body)
        flags = len(payload)
    elif codec == 'zstd':
        payload = bytearray(zstd.compress(body))
        keys = [0xAA55AA55, 0xF00FF00F, 0xAA55AA55, 0x12481248]
        if len(payload) >= 16:
            for i, key in enumerate(keys):
                struct.pack_into('<I', payload, i * 4, struct.unpack_from('<I', payload, i * 4)[0] ^ key)
        if len(payload) >= 32:
            end = (len(payload) // 4 - 4) * 4
            for i, key in enumerate(reversed(keys)):
                struct.pack_into('<I', payload, end + i * 4, struct.unpack_from('<I', payload, end + i * 4)[0] ^ key)
        flags = len(payload) | 0x40000000
    else:
        payload, flags = body, 0
    return struct.pack('<4sIII', b'VRFs', 1129316352, len(body) if declared_size is None else declared_size, flags) + payload


@unittest.skipUnless(zstd is not None, ZSTD_REQUIRED)
class BlkFixtures(unittest.TestCase):
    def test_point2_and_color_are_exact_source_fields(self):
        raw = blk([param(0, 4, 0), param(1, 10, 0xff112233)], struct.pack('<2f', 2048., -1020.))
        root, _ = decode_blk(raw)
        self.assertEqual(root['params'], [{'name': 'position', 'type': 'point2', 'value': [2048., -1020.]},
                                        {'name': 'color', 'type': 'color', 'value': [0x33, 0x22, 0x11, 0xff]}])

    def test_unicode_local_name(self):
        raw = blk([param(0, 2, 42)], name_bytes=('地图'.encode('utf-8'),))
        self.assertEqual(decode_blk(raw)[0]['params'][0]['name'], '地图')

    def test_shared_name_reference(self):
        raw = b'\3' + b'\0\1\1\0' + param(0, 2, 12) + b'\0\1\0'
        self.assertEqual(decode_blk(raw, ['地图'])[0]['params'][0]['name'], '地图')

    def test_invalid_utf8_name_is_rejected(self):
        with self.assertRaises(ValueError):
            decode_blk(blk([param(0, 2, 1)], name_bytes=(b'\xff',)))

    def test_parameter_name_out_of_range(self):
        with self.assertRaises(ValueError):
            decode_blk(blk([param(2, 2, 1)]))

    def test_unknown_parameter_type(self):
        with self.assertRaises(ValueError):
            decode_blk(blk([param(0, 14, 1)]))

    def test_complex_vector_does_not_read_past_data(self):
        with self.assertRaises(ValueError):
            decode_blk(blk([param(0, 5, 0)], struct.pack('<2f', 0., 1.)))

    def test_nonfinite_scalar_is_rejected(self):
        with self.assertRaises(ValueError):
            decode_blk(blk([param(0, 3, 0x7fc00000)]))

    def test_nonboolean_word_is_rejected(self):
        with self.assertRaises(ValueError):
            decode_blk(blk([param(0, 9, 2)]))

    def test_cycle_or_uncovered_block_is_rejected(self):
        with self.assertRaises(ValueError):
            decode_blk(blk([], block=b'\0\0\1\0'))

    def test_trailing_decoded_bytes_are_rejected(self):
        with self.assertRaises(ValueError):
            decode_blk(blk([]) + b'\0')

    def test_varuint_uint32_overflow_is_rejected(self):
        with self.assertRaises(ValueError):
            Reader(b'\xff\xff\xff\xff\x10').varuint()

    def test_truncated_shared_namemap_is_rejected(self):
        with self.assertRaises(ValueError):
            shared_context({'\xff?nm': b'\0' * 39})


@unittest.skipUnless(zstd is not None, ZSTD_REQUIRED)
class VromFixtures(unittest.TestCase):
    def decode(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.vromfs.bin'
            path.write_bytes(data)
            return read_vromfs(path)[0]

    def test_utf8_filename_preserved(self):
        self.assertEqual(self.decode(vrom(vrom_body([('地图.blk'.encode('utf-8'), b'payload')]))), {'地图.blk': b'payload'})

    def test_reserved_binary_namemap_key_preserved(self):
        self.assertEqual(self.decode(vrom(vrom_body([(b'\xff?nm', b'payload')]))), {'\xff?nm': b'payload'})

    def test_invalid_utf8_path_is_rejected(self):
        with self.assertRaises(ValueError):
            self.decode(vrom(vrom_body([(b'\xc3(', b'payload')])))

    def test_all_three_storage_modes(self):
        body = vrom_body([(b'a.blk', b'point2' * 100)])
        for codec in (None, 'zlib', 'zstd'):
            with self.subTest(codec=codec):
                self.assertEqual(self.decode(vrom(body, codec)), {'a.blk': b'point2' * 100})

    def test_duplicate_names_are_rejected(self):
        with self.assertRaises(ValueError):
            self.decode(vrom(vrom_body([(b'a', b'1'), (b'a', b'2')])))

    def test_file_data_range_is_bounded(self):
        body = bytearray(vrom_body([(b'a', b'x')]))
        struct.pack_into('<I', body, 44, 1000)
        with self.assertRaises(ValueError):
            self.decode(vrom(body))

    def test_table_range_is_bounded(self):
        body = bytearray(vrom_body([(b'a', b'x')]))
        struct.pack_into('<I', body, 0, len(body))
        with self.assertRaises(ValueError):
            self.decode(vrom(body))

    def test_compressed_output_cannot_exceed_declared_size(self):
        body = vrom_body([(b'a', b'x' * 10000)])
        for codec in ('zlib', 'zstd'):
            with self.subTest(codec=codec), self.assertRaises(ValueError):
                self.decode(vrom(body, codec, declared_size=32))

    def test_zstd_truncation_and_trailing_bytes_rejected(self):
        packed = zstd.compress(b'coordinates')
        for changed in (packed[:-1], packed + b'junk'):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                bounded_zstd(changed, 100)

    def test_truncated_extended_header_rejected(self):
        with self.assertRaises(ValueError):
            self.decode(struct.pack('<4sIII', b'VRFx', 1129316352, 32, 0))


if __name__ == '__main__':
    unittest.main()
