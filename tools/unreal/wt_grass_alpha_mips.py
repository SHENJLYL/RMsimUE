"""Strict, stdlib-only binding and native Source readback for original grass alpha mips.

R8 DDS stores the decoded original BC4 mip chain. Unreal keeps these mips and
uses uncompressed BGRA8 at build time; this module does not claim GPU-byte readback.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import struct

SCHEMA = 'wt_grass_source_alpha_mips_v1'
ADAPTER_SCHEMA = 'wt_grass_alpha_mip_adapter_v1'


def _require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _file(base, relative, digest):
    name = Path(relative)
    _require(not name.is_absolute() and '..' not in name.parts, 'alpha mip path escapes its package')
    path = (base / name).resolve(strict=True)
    _require(path.is_relative_to(base.resolve()), 'alpha mip path escapes its package')
    _require(sha(path) == digest, 'alpha mip SHA differs: ' + relative)
    return path


def parse_r8_dds(data):
    """Accept only one uncompressed, tightly packed, complete R8 2D mip chain."""
    _require(len(data) >= 128 and data[:4] == b'DDS ', 'invalid R8 DDS signature/header')
    size, flags, height, width, pitch, depth, count = struct.unpack_from('<7I', data, 4)
    pf_size, pf_flags, fourcc, bits, red, green, blue, alpha = struct.unpack_from('<8I', data, 76)
    caps, caps2 = struct.unpack_from('<2I', data, 108)
    _require(size == 124 and pf_size == 32 and flags & 0x1007 == 0x1007,
             'invalid R8 DDS header flags')
    _require(0 < width <= 16384 and 0 < height <= 16384 and
             width & (width - 1) == 0 and height & (height - 1) == 0,
             'grass R8 DDS dimensions must be powers of two')
    _require(depth in (0, 1) and caps2 == 0 and caps & 0x1000,
             'grass alpha DDS must be one 2D texture')
    _require(count == max(width, height).bit_length(), 'grass alpha DDS must retain every original mip')
    _require(not flags & 8 or pitch == width, 'padded R8 DDS rows are unsupported')
    offset = 128
    dx10 = int.from_bytes(b'DX10', 'little')
    if fourcc == dx10 and pf_flags & 4:
        _require(len(data) >= 148, 'truncated R8 DX10 header')
        fmt, dimension, misc, array, misc2 = struct.unpack_from('<5I', data, 128)
        _require(fmt == 61 and dimension == 3 and misc == 0 and array == 1 and misc2 == 0,
                 'DDS is not linear R8_UNORM single Texture2D')
        offset = 148
        encoding = 'DXGI_FORMAT_R8_UNORM'
    else:
        # UE WriteDDS may select its equivalent legacy D3DFMT_L8 description.
        luminance = (pf_flags & 0x20000 and bits == 8 and red == 255 and
                     green == blue == alpha == 0 and fourcc == 0)
        legacy_l8 = pf_flags == 4 and fourcc == 50
        _require(luminance or legacy_l8, 'DDS is not uncompressed legacy L8/R8')
        encoding = 'D3DFMT_L8'
    mips = []
    for level in range(count):
        w, h = max(1, width >> level), max(1, height >> level)
        end = offset + w * h
        _require(end <= len(data), 'truncated grass alpha mip ' + str(level))
        pixels = data[offset:end]
        mips.append(dict(level=level, size=[w, h], bytes=len(pixels),
                         pixel_sha256=hashlib.sha256(pixels).hexdigest(), pixels=pixels))
        offset = end
    _require(offset == len(data), 'unexpected trailing grass alpha DDS bytes')
    return dict(width=width, height=height, mip_count=count, encoding=encoding, mips=mips)


def write_r8_dds(width, height, mips):
    header = bytearray(148)
    header[:4] = b'DDS '
    struct.pack_into('<7I', header, 4, 124, 0x2100F, height, width, width, 0, len(mips))
    struct.pack_into('<8I', header, 76, 32, 4, int.from_bytes(b'DX10', 'little'), 0, 0, 0, 0, 0)
    struct.pack_into('<5I', header, 108, 0x401008, 0, 0, 0, 0)
    struct.pack_into('<5I', header, 128, 61, 3, 0, 1, 0)
    result = bytes(header) + b''.join(mips)
    parse_r8_dds(result)
    return result


def validate_alpha_mips(base, foliage):
    """Bind the additive alpha contract while proving all previous foliage fields stable."""
    adapter = foliage.get('alpha_mip_adapter')
    if adapter is None:
        return None
    base = Path(base).resolve(strict=True)
    _require(adapter.get('schema') == ADAPTER_SCHEMA, 'unsupported grass alpha mip adapter')
    path = _file(base, adapter['definition_file'], adapter['definition_sha256'])
    proof = json.loads(path.read_text(encoding='utf-8-sig'))
    _require(proof.get('schema') == SCHEMA and proof.get('status') == 'passed',
             'original alpha mip proof has not passed')
    _require(proof['source_level_sha256'] == foliage['source_level_sha256'] and
             proof['clip_value'] == foliage['alpha_clip_value'] == .5,
             'alpha clip/source identity differs')
    for source in proof['source_records']:
        _file(path.parent, source['file'], source['sha256'])
    baseline_path = _file(path.parent, proof['baseline_definition_file'], proof['baseline_definition_sha256'])
    baseline = json.loads(baseline_path.read_text(encoding='utf-8-sig'))
    candidate = dict(foliage)
    for field in ('alpha_clip_value', 'alpha_clip_provenance', 'alpha_mip_adapter'):
        candidate.pop(field, None)
        baseline.pop(field, None)
    _require(candidate == baseline, 'alpha mip adaptation changed unrelated foliage data')
    models = {model['id']: model for model in foliage['models']}
    rows = proof['textures']
    _require(len(rows) == len(models) == 10 and {row['model_id'] for row in rows} == set(models),
             'alpha mip proof must bind all ten source models once')
    records = {}
    for row in rows:
        model = models[row['model_id']]
        material = model['material']
        _require(row['prepared_png_sha256'] == material['alpha_texture_sha256'] and
                 row['top_mip_exact_equals_prepared_png'] is True and
                 row['all_source_dds_mip_bytes_accounted'] is True and
                 row['source_gamma_equal_one'] is True and row['channel'] == 'R',
                 'original alpha PNG or mip accounting differs')
        _file(base, material['alpha_texture'], row['prepared_png_sha256'])
        expected_address = material.get('texture_address', 'wrap')
        _require(row['ue_address_u'] == row['ue_address_v'] == expected_address,
                 'alpha mip sampler address differs from the existing grass adapter')
        dds_path = _file(path.parent, row['dds_file'], row['dds_sha256'])
        actual = parse_r8_dds(dds_path.read_bytes())
        _require([actual['width'], actual['height']] == row['size'] and
                 actual['mip_count'] == row['source_levels'] == len(row['mips']),
                 'alpha source mip dimensions/count differ')
        for got, wanted in zip(actual['mips'], row['mips']):
            _require(all(got[key] == wanted[key] for key in ('level', 'size', 'bytes', 'pixel_sha256')),
                     'alpha source mip bytes differ: ' + row['model_id'])
        records[row['model_id']] = dict(row, path=dds_path)
    return dict(definition=proof, definition_path=path, definition_sha256=sha(path), records=records)


def verify_native_alpha_texture(unreal, texture, record, output_directory):
    """Read settings and export actual saved/imported UTexture.Source, without saving assets."""
    _require(isinstance(texture, unreal.Texture2D), 'native grass alpha Texture2D missing')
    address = unreal.TextureAddress.TA_CLAMP if record['ue_address_u'] == 'clamp' else unreal.TextureAddress.TA_WRAP
    expected = dict(srgb=False, virtual_texture_streaming=False, lod_bias=0, max_texture_size=0,
        mip_gen_settings=unreal.TextureMipGenSettings.TMGS_LEAVE_EXISTING_MIPS,
        do_scale_mips_for_alpha_coverage=False,
        compression_settings=unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP,
        address_x=address, address_y=address)
    actual = {key: texture.get_editor_property(key) for key in expected}
    _require(all(actual[key] == value for key, value in expected.items()),
             'native alpha settings differ: ' + record['model_id'])
    digest = unreal.EditorAssetLibrary.get_metadata_tag(texture, 'RMSimGrassSourceSHA256')
    _require(digest == record['dds_sha256'], 'native alpha DDS identity differs')
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / (record['model_id'] + '.dds')
    _require(not destination.exists(), 'use a fresh native alpha readback directory')
    task = unreal.AssetExportTask()
    task.set_editor_property('object', texture)
    task.set_editor_property('exporter', unreal.TextureExporterDDS())
    task.set_editor_property('filename', str(destination))
    task.set_editor_property('automated', True)
    task.set_editor_property('prompt', False)
    task.set_editor_property('replace_identical', False)
    _require(unreal.Exporter.run_asset_export_task(task) and destination.is_file(),
             'native alpha Source DDS export failed: ' + record['model_id'])
    result = parse_r8_dds(destination.read_bytes())
    _require(result['mip_count'] == record['source_levels'], 'native Source dropped alpha mip levels')
    mips = [{key: item[key] for key in ('level', 'size', 'bytes', 'pixel_sha256')} for item in result['mips']]
    expected_mips = [{key: item[key] for key in ('level', 'size', 'bytes', 'pixel_sha256')} for item in record['mips']]
    _require(mips == expected_mips, 'native Source alpha mip pixels differ: ' + record['model_id'])
    return dict(status='passed', model_id=record['model_id'], asset=texture.get_path_name(),
        source_dds_sha256=record['dds_sha256'], exported_dds_file=str(destination),
        exported_dds_sha256=sha(destination), mip_count=result['mip_count'], mips=mips,
        native_settings={key: str(value) for key, value in actual.items()},
        scope='UTexture.Source full-chain pixels and native build settings; no platform/GPU byte readback')
