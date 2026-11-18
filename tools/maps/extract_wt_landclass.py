"""Recover original lmap class colors and packed weight atlas, without game writes.

Python 3.14, NumPy, Pillow and the installed Oodle library are required. Output is
fresh and independent of the scene package; prepare_wt_landclass.py publishes it.
"""
import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'unreal'))
from wt_landclass import (CLASS_NAMES, COLOR_NAMES, DAGOR_COMMIT, PACKAGE_SCHEMA,
                          PROOF_SCHEMA, shader_vectors)
from dagor_codec import configure_oodle
from extract_wt_terrain import chunks
from export_wt_textures import decode_texture


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n', encoding='utf-8')


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, count):
        if count < 0 or self.pos + count > len(self.data):
            raise ValueError(('source boundary', self.pos, count, len(self.data)))
        value = self.data[self.pos:self.pos + count]
        self.pos += count
        return value

    def u32(self):
        return struct.unpack('<I', self.take(4))[0]

    def varuint(self):
        value = 0
        for shift in range(0, 35, 7):
            byte = self.take(1)[0]
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError('invalid unsigned variable integer')

    def string(self):
        count = self.u32()
        value = self.take(count).decode('utf-8')
        if any(self.take((-count) % 4)):
            raise ValueError('nonzero source string alignment')
        return value


def read_blk(data):
    """Dagor blk_serialize.cpp full binary v1, with zero-based parameter name IDs."""
    r = Reader(data)
    if r.take(1) != b'\x01':
        raise ValueError('unsupported embedded landclass DataBlock')
    count, size = r.varuint(), r.varuint()
    names = r.take(size).decode('utf-8').split('\0')
    if names.pop() != '' or len(names) != count:
        raise ValueError('DataBlock names boundary')
    block_count, param_count, data_size = r.varuint(), r.varuint(), r.varuint()
    values, params = r.take(data_size), r.take(param_count * 8)
    blocks, offset = [], 0

    def decode(flags, value):
        kind = flags >> 24
        if kind == 1:
            if value & 0x80000000:
                return names[value & 0x7fffffff]
            return values[value:values.index(0, value)].decode('utf-8')
        if kind == 2:
            return struct.unpack('<i', struct.pack('<I', value))[0]
        if kind == 3:
            return struct.unpack('<f', struct.pack('<I', value))[0]
        if kind in (4, 5, 6):
            return list(struct.unpack_from('<' + 'f' * (kind-2), values, value))
        if kind == 9:
            return bool(value)
        raise ValueError(('unsupported landclass parameter type', kind))

    for _ in range(block_count):
        name, pc, bc = r.varuint(), r.varuint(), r.varuint()
        first = r.varuint() if bc else 0
        fields = []
        for _ in range(pc):
            flags, value = struct.unpack_from('<II', params, offset)
            offset += 8
            fields.append(dict(name=names[flags & 0xffffff], type=flags >> 24, value=decode(flags, value)))
        blocks.append(dict(name=names[name-1] if name else None, params=fields, children_range=[first, bc]))
    if r.pos != len(data) or offset != len(params):
        raise ValueError('DataBlock trailing/missing bytes')

    def tree(index):
        item = blocks[index]
        first, count = item['children_range']
        return dict(name=item['name'], params=item['params'], children=[tree(first+i) for i in range(count)])

    return tree(0)


def read_lmap(source):
    payload = next(b for name, _, b in chunks(source) if name == 'lmap')
    h = struct.unpack_from('<4sIff4iI4I', payload)
    if h[:9] != (b'lndm', 4, 16.0, 1024.0, 32, 32, -16, -16, 1):
        raise ValueError('unreviewed lmap domain/version')
    detail, tile = 36+h[-3], 36+h[-2]
    r = Reader(payload)
    r.pos = detail
    size = r.u32()
    end, count = r.pos+size, r.u32()
    classes, raw_classes = [], []
    for i in range(count):
        record = r.take(r.u32())
        item = Reader(record)
        name = item.string()
        data = read_blk(item.take(len(record)-item.pos))
        values = {p['name']: p['value'] for p in data['params']}
        if values['size'][0] != values['size'][1]:
            raise ValueError('non-square original color scale')
        classes.append(dict(id=i, name=name, texture_name=values['texture'].split('*')[0],
                            size_m=values['size'][0], offset_m=values.get('offset', [0.0, 0.0]),
                            source_record_sha256=sha(record)))
        raw_classes.append(dict(id=i, name=name, sha256=sha(record), data=data))
    if r.pos != end or tuple(x['name'] for x in classes) != CLASS_NAMES:
        raise ValueError('source class table differs')
    if tuple(x['texture_name'] for x in classes) != COLOR_NAMES:
        raise ValueError('source class color table differs')
    sx, sy, size, elem = struct.unpack('<4i', r.take(16))
    if (sx, sy, size, elem) != (32, 32, 0, 64):
        raise ValueError('expected packed source weight atlas')
    offsets = struct.unpack('<1024I', r.take(4096))
    if len(set(offsets)) != 1 or 36+offsets[0] != r.pos or r.take(15) != b'\xff'*7+b'\0'*8:
        raise ValueError('legacy compatibility stub differs')
    pages_per_row, page_count = r.u32(), r.u32()
    raw_records = r.take(1024*12)
    cells = []
    for i in range(1024):
        raw = raw_records[i*12:(i+1)*12]
        packed = struct.unpack_from('<I', raw)[0]
        count = ((packed >> 28) & 7)+1
        ids = list(raw[4:11])
        pages = [packed & 0x3fff, (packed >> 14) & 0x3fff]
        if not 1 <= count <= 4 or any(x >= 5 for x in ids[:count]) or ids[count:] != [255]*(7-count):
            raise ValueError(('unreviewed cell classes', i, count, ids))
        if count > 1 and pages[0] >= page_count:
            raise ValueError('source page index exceeds atlas')
        cells.append(dict(cell=i, xy=[i % 32, i//32], class_ids=ids, blend_count=count, pages=pages))
    ddsx = r.take(r.u32())
    if r.pos != tile or (pages_per_row, page_count) != (112, 448):
        raise ValueError('packed atlas/next source block boundary differs')
    return dict(domain=dict(cell_m=h[3], grid_m=h[2], cells=[sx, sy], origin_cells=[h[6], h[7]]),
                classes=classes, cells=cells, raw_classes=raw_classes, raw_records=raw_records,
                ddsx=ddsx, source_lmap_sha256=sha(payload), detail_offset=detail, tile_offset=tile)


def bilinear(image, uv, wrap=False):
    h, w = image.shape[:2]
    p = uv * np.array([w, h]) - 0.5
    lo = np.floor(p).astype(np.int64)
    f = p-lo
    if wrap:
        x0, x1 = lo[:, 0] % w, (lo[:, 0]+1) % w
        y0, y1 = lo[:, 1] % h, (lo[:, 1]+1) % h
    else:
        x0, x1 = np.clip(lo[:, 0], 0, w-1), np.clip(lo[:, 0]+1, 0, w-1)
        y0, y1 = np.clip(lo[:, 1], 0, h-1), np.clip(lo[:, 1]+1, 0, h-1)
    return ((image[y0, x0]*(1-f[:, 0, None])+image[y0, x1]*f[:, 0, None])*(1-f[:, 1, None]) +
            (image[y1, x0]*(1-f[:, 0, None])+image[y1, x1]*f[:, 0, None])*f[:, 1, None])


def check_weights(data, atlas_rgb, meta0, meta1):
    """Compare original packed uint records against the transported RGBA lookups."""
    rng = np.random.default_rng(427)
    points = np.concatenate([rng.uniform(-16384, 16368, (16384, 2)),
                             np.array([[x*1024-16384+512, y*1024-16384+512]
                                       for y in range(32) for x in range(32)])])
    cf = points/1024+16
    cell = np.floor(cf).astype(int)
    frac = cf-cell
    indices = cell[:, 1]*32+cell[:, 0]
    records = np.frombuffer(data['raw_records'], dtype='<u4').reshape(1024, 3)[indices, 0]
    count = ((records >> 28) & 7)+1
    page = records & 0x3fff
    ids = np.array([x['class_ids'][:4] for x in data['cells']])[indices]
    m = meta0[cell[:, 1], cell[:, 0]].astype(int)
    n = meta1[cell[:, 1], cell[:, 0]].astype(int)
    if not (np.array_equal(page, m[:, 0]+256*m[:, 1]) and np.array_equal(count, m[:, 2]) and
            np.array_equal(ids, np.column_stack([m[:, 3], n[:, :3]]))):
        raise ValueError('packed cell transport differs')
    uv = (np.column_stack([page % 112, page//112])*68 + 2 + frac*64)/[7616, 272]
    sampled = bilinear(atlas_rgb.astype(np.float32)/255, uv)
    slot_weights = np.zeros((len(points), 4))
    for k in range(3):
        slot_weights[:, k] = sampled[:, k]*(k<count-1)
    last = np.maximum(0, 1-slot_weights.sum(axis=1))
    slot_weights[np.arange(len(points)), count-1] += last
    global_weights = np.stack([(slot_weights*(ids==k)).sum(axis=1) for k in range(5)], axis=1)
    if not np.isfinite(global_weights).all() or global_weights.min() < 0 or global_weights.max() > 1:
        raise ValueError('nonfinite/out-of-range decoded source weights')
    return dict(samples=len(points), class_weight_min=global_weights.min(axis=0).tolist(),
                class_weight_max=global_weights.max(axis=0).tolist(),
                source_sum_min=float(global_weights.sum(axis=1).min()),
                source_sum_max=float(global_weights.sum(axis=1).max()),
                packed_lookup_roundtrip_max_error=0,
                formula='last=saturate(1-sum(preceding page channels)); no renormalization')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--map', type=Path, required=True)
    p.add_argument('--game-root', type=Path, required=True)
    p.add_argument('--dae-root', type=Path, required=True)
    p.add_argument('--oodle-library', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    target = out/'landclass'
    (target/'textures').mkdir(parents=True)
    configure_oodle(a.oodle_library)
    sys.path.insert(0, str(a.dae_root.resolve()))
    from parse.material import DDSx, DDSxTexturePack2
    import util.log as log
    log.log = lambda *args, **kwargs: None
    source = a.map.read_bytes()
    data = read_lmap(source)
    (out/'weights.ddsx').write_bytes(data['ddsx'])
    atlas_image, floats = decode_texture(DDSx(str(out/'weights.ddsx')))
    if floats is not None or atlas_image.size != (7616, 272):
        raise ValueError('weight DDSx decoded dimensions differ')
    atlas_rgb = np.asarray(atlas_image.convert('RGB'))
    meta0, meta1 = np.zeros((32, 32, 4), dtype=np.uint8), np.full((32, 32, 4), 255, dtype=np.uint8)
    for item in data['cells']:
        x, y = item['xy']
        page = item['pages'][0]
        meta0[y, x] = [page & 255, page >> 8, item['blend_count'], item['class_ids'][0]]
        meta1[y, x, :3] = item['class_ids'][1:4]
    textures = {}

    def export(parameter, name, image, srgb, nearest=False, origin=None):
        path = target/'textures'/(name+'.png')
        image.save(path)
        actual = np.asarray(Image.open(path))
        if not np.array_equal(actual, np.asarray(image)):
            raise ValueError('PNG decoded pixel transport differs: ' + name)
        textures[parameter] = dict(name=name, file='landclass/textures/'+path.name, sha256=sha(path.read_bytes()),
                                   size=list(image.size), srgb=srgb, nearest=nearest, no_mips=not srgb,
                                   address='wrap' if srgb else 'clamp', source=origin)

    export('WT_LandWeights', 'WT_LandWeights', Image.fromarray(atlas_rgb), False,
           origin=dict(lmap_ddsx_sha256=sha(data['ddsx']), decoded_rgb_sha256=sha(atlas_rgb.tobytes())))
    export('WT_LandCells0', 'WT_LandCells0', Image.fromarray(meta0), False, True,
           origin=dict(lmap_records_sha256=sha(data['raw_records'])))
    export('WT_LandCells1', 'WT_LandCells1', Image.fromarray(meta1), False, True,
           origin=dict(lmap_records_sha256=sha(data['raw_records'])))
    wanted, found = set(COLOR_NAMES), {}
    roots = [a.game_root/'content/base/res', a.game_root/'content.hq/hq_tex/res',
             a.game_root/'content.hq/uhq_environment/res']
    paths = sorted({path for root in roots for path in root.glob('*.dxp.bin')})
    for index, path in enumerate(paths):
        pack = DDSxTexturePack2(str(path))
        for tex in pack.getPackedFiles():
            if tex.name in wanted and (tex.name not in found or tex.getPixelCnt() > found[tex.name].getPixelCnt()):
                found[tex.name] = tex
        if (index+1) % 100 == 0:
            print(f'Landclass texture packs: {index+1}/{len(paths)}', flush=True)
    if set(found) != wanted:
        raise ValueError('missing original landclass colors: ' + str(sorted(wanted-set(found))))
    asymmetric = []
    points = np.array([[128.0, 640.0], [510.0, 1530.0], [1790.0, 360.0]])
    for i, name in enumerate(COLOR_NAMES):
        tex = found[name]
        header = tex._DDSx__header
        if header.flags & 0x8000 or header.flags & 255 != 0x11:
            raise ValueError(('unreviewed source color sampler/gamma', name, hex(header.flags)))
        image, floats = decode_texture(tex)
        if floats is not None:
            raise ValueError('source landclass color is floating data')
        packed = tex.getBin().read()
        # DAE calls the stored width 'h' and height 'w'; serialize original order.
        raw_header = struct.pack('<4s4sIHHBBHHBBII', header.label, header.d3dformat,
            header.flags | (header.cMethod << 24), header.h, header.w,
            header.levels, header.hqPartLevels, header.depth, header.bitsPerPixel,
            (header.lQmip << 4) | header.mQmip, (header.dxtShift << 4) | header.uQmip,
            header.memSz, header.packedSz)
        origin = dict(pack=str(tex.filePath), offset=tex.offset, packed_bytes=len(packed), packed_sha256=sha(packed),
                      source_header_hex=raw_header.hex(), flags=header.flags)
        export('LC'+str(i), name, image, True, origin=origin)
        if i == 1:
            rgb = np.asarray(image.convert('RGB')).astype(np.float32)/255
            positive = points/2048
            negative = points*[1, -1]/2048
            a_rgb, b_rgb = bilinear(rgb, positive, True), bilinear(rgb, negative, True)
            asymmetric = [dict(dagor_xz_m=points[k].tolist(), previous_positive_v_uv=positive[k].tolist(),
                               source_negative_v_uv=negative[k].tolist(), previous_rgb=a_rgb[k].tolist(),
                               source_rgb=b_rgb[k].tolist()) for k in range(len(points))]
    atlas = dict(element_size=64, page_size=68, pages_per_row=112, page_count=448, dimensions=[7616, 272],
                 ddsx_sha256=sha(data['ddsx']), decoded_rgb_sha256=sha(atlas_rgb.tobytes()))
    result = dict(schema=PACKAGE_SCHEMA, source_sha256=sha(source), source_lmap_sha256=data['source_lmap_sha256'],
                  dagor_commit=DAGOR_COMMIT, domain=data['domain'], atlas=atlas,
                  classes=data['classes'], cells=data['cells'], textures=textures)
    proof = {key: result[key] for key in ('source_sha256', 'source_lmap_sha256', 'domain', 'atlas', 'classes', 'textures')}
    proof.update(schema=PROOF_SCHEMA, status='passed',
                 source_file=str(a.map.resolve()), source_detail_offset=data['detail_offset'], source_tile_offset=data['tile_offset'],
                 cells_sha256=sha(json.dumps(data['cells'], sort_keys=True, separators=(',', ':')).encode()),
                 source_records_sha256=sha(data['raw_records']),
                 checks=dict(source_boundaries_exact=True, packed_records_roundtrip_exact=True,
                             atlas_decoded_pixels_exact=True, weights_source_formula_checked=True),
                 weight_checks=check_weights(data, atlas_rgb, meta0, meta1),
                 negative_v_samples=asymmetric, shader_vectors=shader_vectors(result),
                 source_references=[
                     'prog/gameLibs/landMesh/lmeshLandClasses.cpp:load_land_class/loadLandClasses',
                     'prog/gameLibs/landMesh/lmeshRenderer.cpp:prepareCell',
                     'prog/gameLibs/landMesh/lmeshWeightAtlas.cpp:readPacked',
                     'prog/gameLibs/landMesh/shaders/land_weight_inc.dshl:get_weights',
                     'prog/daNetGame/shaders/make_clipmap.dshl:landmesh_mega_clipmap_vs'],
                 scope='Original global macro colors and exact packed source weights; shader microdetails are separate')
    save_json(target/'verification.json', proof)
    result.update(verification_file='landclass/verification.json', verification_sha256=sha((target/'verification.json').read_bytes()))
    save_json(target/'landclass.json', result)
    save_json(out/'source-classes.json', data['raw_classes'])
    save_json(out/'verification.json', dict(schema='wt_landclass_extraction_v1', status='passed',
        source_sha256=result['source_sha256'], manifest_sha256=sha((target/'landclass.json').read_bytes()),
        verification_sha256=result['verification_sha256'], class_count=5, cell_count=1024,
        texture_count=8, atlas_pages=448, negative_v_samples=asymmetric, weight_checks=proof['weight_checks']))
    print(json.dumps(dict(status='passed', output=str(out), texture_count=8, class_count=5,
                          manifest_sha256=sha((target/'landclass.json').read_bytes())), indent=2))


if __name__ == '__main__':
    main()
