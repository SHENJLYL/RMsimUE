"""Read-only Dagor compression boundary using stdlib codecs and an installed Oodle library.

No game-process access, downloaded decoder binary, or source-game writes are used.
The Oodle ABI is checked against the installed UE EpicGames.Oodle/Oodle.cs declaration.
"""
from compression import zstd
import ctypes
import lzma
from pathlib import Path
import struct
import zlib

_oodle = None


def configure_oodle(library):
    global _oodle
    path = Path(library).resolve(strict=True)
    dll = ctypes.CDLL(str(path))
    fn = dll.OodleLZ_Decompress
    ptr, num, integer = ctypes.c_void_p, ctypes.c_int64, ctypes.c_int
    fn.argtypes = [ptr, num, ptr, num, integer, integer, integer,
                  ptr, num, ptr, ptr, ptr, num, integer]
    fn.restype = ctypes.c_int64
    _oodle = fn


def oodle_decompress(data, expected=None):
    if expected is None:
        if len(data) < 4:
            raise ValueError("missing Oodle size prefix")
        expected = struct.unpack_from('<I', data)[0]
        data = data[4:]
    if _oodle is None:
        raise RuntimeError("configure an installed Oodle decoder library before reading this map")
    if not 0 < expected <= 512*1024*1024 or not data:
        raise ValueError("invalid/beyond-limit Oodle output size")
    source = ctypes.create_string_buffer(data)
    target = ctypes.create_string_buffer(expected)
    size = _oodle(source, len(data), target, expected, 1, 0, 0, None, 0, None, None, None, 0, 3)
    if size != expected:
        raise ValueError(f"Oodle decoded {size} bytes; expected {expected}")
    return target.raw


def lzma_decompress(data):
    if len(data) < 5:
        raise ValueError("missing LZMA properties")
    prop = data[0]
    if prop >= 9*5*5:
        raise ValueError("invalid LZMA properties")
    filters = [dict(id=lzma.FILTER_LZMA1, dict_size=struct.unpack_from('<I', data, 1)[0],
                    lc=prop % 9, lp=(prop // 9) % 5, pb=prop // 45)]
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters)
    result = decoder.decompress(data[5:], max_length=512*1024*1024)
    if len(result) >= 512*1024*1024:
        raise ValueError("LZMA output exceeds limit")
    return result


def decompress(data, flags, expected=None):
    if flags == 0x80:
        result = oodle_decompress(data, expected)
    elif flags == 0x40:
        result = zstd.decompress(data)
    elif flags == 0x60:
        result = zlib.decompress(data)
    elif flags == 0x20 or flags == 0:
        result = lzma_decompress(data)
    else:
        raise ValueError(f"unsupported compression flag {flags:#x}")
    if expected is not None and len(result) != expected:
        raise ValueError(f"decoded size {len(result)} != {expected}")
    return result


def framed(data, offset, expected=None):
    value = struct.unpack_from('<I', data, offset)[0]
    length = value & 0x3fffffff
    end = offset+4+length
    if end > len(data):
        raise ValueError("compressed block exceeds input")
    return decompress(data[offset+4:end], (value >> 30) << 6, expected), end
