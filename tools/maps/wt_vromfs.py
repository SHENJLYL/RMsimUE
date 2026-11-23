"""Read the little-endian Dagor VROMFS container without modifying the archive.

Layout reference: GaijinEntertainment/DagorEngine, pinned by the source audit.
Only archive storage is decoded; packed BLK data remains a separate boundary.
"""
from pathlib import Path
import hashlib
import struct
import zlib
from compression import zstd

LIMIT = 512 * 1024 * 1024


def bounded_zstd(payload, limit, dictionary=None):
    """Decode one complete source frame without exceeding its declared limit."""
    decoder = zstd.ZstdDecompressor(zstd_dict=zstd.ZstdDict(dictionary) if dictionary is not None else None)
    data = decoder.decompress(payload, max_length=limit + 1)
    if len(data) > limit:
        raise ValueError('Zstandard decoded data exceeds its size limit')
    if not decoder.eof:
        raise ValueError('Truncated Zstandard source frame')
    if decoder.unused_data:
        raise ValueError('Trailing bytes after Zstandard source frame')
    return data


def read_vromfs(path):
    path = Path(path)
    raw = path.read_bytes()
    if len(raw) < 16:
        raise ValueError('VROMFS header truncated')
    magic, target, full_size, packed_flags = struct.unpack_from('<4sIII', raw)
    if magic not in (b'VRFs', b'VRFx') or not 32 <= full_size <= LIMIT:
        raise ValueError('Unsupported VROMFS header/size')
    header_size = 16
    ext_flags = version = 0
    if magic == b'VRFx':
        if len(raw) < 24:
            raise ValueError('VROMFS extension truncated')
        ext_size, ext_flags, version = struct.unpack_from('<HHI', raw, 16)
        if not 8 <= ext_size <= 4096:
            raise ValueError('Invalid VROMFS extension size')
        header_size += ext_size
    packed_size = packed_flags & 0x3fffffff
    body_size = packed_size or full_size
    if header_size + body_size > len(raw):
        raise ValueError('VROMFS body exceeds source')
    payload = raw[header_size:header_size+body_size]
    if packed_size and packed_flags & 0x40000000:
        data = bytearray(payload)
        keys = [0xAA55AA55,0xF00FF00F,0xAA55AA55,0x12481248]
        if len(data) >= 16:
            for i,key in enumerate(keys):
                struct.pack_into('<I',data,4*i,struct.unpack_from('<I',data,4*i)[0]^key)
        if len(data) >= 32:
            offset = (len(data)//4-4)*4
            for i,key in enumerate(reversed(keys)):
                struct.pack_into('<I',data,offset+4*i,struct.unpack_from('<I',data,offset+4*i)[0]^key)
        body = bounded_zstd(data, full_size)
    elif packed_size:
        decoder = zlib.decompressobj()
        body = decoder.decompress(payload, full_size + 1)
        if len(body) > full_size:
            raise ValueError('Zlib decoded data exceeds declared VROMFS size')
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('Truncated or trailing VROMFS zlib data')
    else:
        body = payload
    if len(body) != full_size:
        raise ValueError('VROMFS decoded size differs')
    names_offset,count,names_pad = struct.unpack_from('<IIQ',body,0)
    entries_offset,entries_count,entries_pad = struct.unpack_from('<IIQ',body,16)
    if count != entries_count or count > 1000000 or names_offset+count*8 > len(body) or entries_offset+count*16 > len(body):
        raise ValueError(f'Invalid VROMFS file tables: {names_offset=} {count=} {names_pad=} {entries_offset=} {entries_count=} {entries_pad=} body={body[:64].hex()}')
    files = {}
    for i in range(count):
        name_offset, = struct.unpack_from('<Q',body,names_offset+8*i)
        if name_offset >= len(body):
            raise ValueError('Invalid VROMFS filename pointer')
        name_end = body.find(b'\0',name_offset,min(len(body),name_offset+4096))
        if name_end < 0:
            raise ValueError('Unterminated VROMFS filename')
        raw_name = body[name_offset:name_end]
        if not raw_name:
            raise ValueError('Empty VROMFS filename')
        # Dagor's shared-namemap key starts with the deliberately non-UTF8 byte FF.
        # Ordinary paths are UTF8; latin-1 would silently corrupt their names.
        name = '\xff?nm' if raw_name == b'\xff?nm' else raw_name.decode('utf-8')
        offset,size,pad = struct.unpack_from('<IIQ',body,entries_offset+16*i)
        if offset+size > len(body) or name in files:
            raise ValueError('Invalid/duplicate VROMFS entry')
        files[name] = body[offset:offset+size]
    info = dict(path=str(path.resolve()),sha256=hashlib.sha256(raw).hexdigest(),
                magic=magic.decode(),target=target,header_size=header_size,extension_flags=ext_flags,
                version=version,packed_size=packed_size,decoded_size=full_size,
                body_sha256=hashlib.sha256(body).hexdigest(),file_count=count)
    return files,info
