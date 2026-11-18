"""Prepare original Flanders ground microdetail slots for a UE surface adapter.

Reads the already verified level BLK decode and original game texture archives.
The 12 source slot indices, duplicate slot, linear RGBA channels and resolution
are preserved. This package does not edit the landclass or scene definition.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys


OFFICIAL_COMMIT = '9639942de5a8e5b01d91977f60ed82cda22d5f41'
LEVEL_SHA = '427c9b06b62af0635fa08f2d6238211b9850caf7717a0773f186e1835be63b8f'
EXPECTED = (
    'microdetail_stone_a_tex_n', 'microdetail_scan_asphalt_tex_n',
    'microdetail_sand_a_tex_n', 'microdetail_soil_a_tex_n',
    'microdetail_generic_granules_a_tex_n', 'microdetail_grass_a_tex_n',
    'microdetail_forest_floor_a_tex_n', 'microdetail_fabric_a_tex_n',
    'microdetail_wood_b_tex_n', 'microdetail_metal_a_tex_n',
    'microdetail_snow_a_tex_n', 'microdetail_generic_granules_a_tex_n',
)
PARAM_DEFAULTS = {'coloring': 1.0, 'porosity': 1.0, 'puddles': 1.0, 'sparkles': 0.0}
REFERENCES = {
    'prog/gameLibs/landMesh/landMicroDetails.cpp': {
        'lines': '85-121', 'purpose': 'slot parameter indices, parameter defaults, ID count scale and UV scale'},
    'prog/gameLibs/landMesh/shaders/land_micro_detail.dshl': {
        'lines': '244-253, 257-335', 'purpose': 'linear RGBA channel semantics, ID sampling and unrotated world XZ UV'},
    'prog/daNetGame/shaders/clipmap.dshl': {
        'lines': '110-178', 'purpose': 'public clipmap decode returns DetailDesc and does not apply the microdetail helper'},
    'prog/daNetGame/shaders/land_aces.dshl': {
        'lines': '294-380', 'purpose': 'public terrain path applies effects metadata, with no full microdetail color/normal application'},
    'prog/daNetGame/shaders/make_clipmap.dshl': {
        'lines': '1395-1423', 'purpose': 'normal-present decal reflection AGRB stores microdetail ID in B; normal-absent branch emits ID zero'},
}


def sha(path: Path) -> str:
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')


def one_parameter(block, name, default=None):
    values = [x for x in block.get('params', []) if x['name'] == name]
    if len(values) > 1:
        raise ValueError('duplicate configuration parameter: ' + name)
    return values[0]['value'] if values else default


def extract_slots(level):
    blocks = [x for x in level['children'] if x['name'] == 'micro_details']
    if len(blocks) != 1:
        raise ValueError('expected exactly one level micro_details block')
    block = blocks[0]
    slots = []
    for parameter_index, item in enumerate(block['params']):
        if item['name'] != 'micro_detail':
            continue
        if item['type'] != 'string' or not re.fullmatch(r'[A-Za-z0-9_.-]+\*', item['value']):
            raise ValueError('unexpected original microdetail texture name/type')
        children = [x for x in block['children'] if x['name'] == item['value']]
        if len(children) > 1:
            raise ValueError('ambiguous source microdetail parameter block')
        params = dict(PARAM_DEFAULTS)
        if children:
            for key in params:
                params[key] = float(one_parameter(children[0], key, params[key]))
        slots.append(dict(index=len(slots), source_parameter_index=parameter_index,
                          source_texture=item['value'], name=item['value'][:-1],
                          params=list(params.values()), named_params=params))
    if tuple(x['name'] for x in slots) != EXPECTED:
        raise ValueError('original Flanders slot order changed')
    if [x['source_parameter_index'] for x in slots] != list(range(len(slots))):
        raise ValueError('CPU uses actual parameter indices; noncontiguous source slots require explicit handling')
    uv_scale = float(one_parameter(block, 'land_micro_details_uv_scale'))
    if uv_scale != 0.5099999904632568:
        raise ValueError('original UV scale changed')
    return block, slots, uv_scale


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--level-json', type=Path, required=True)
    parser.add_argument('--source-record', type=Path, required=True)
    parser.add_argument('--map-package', type=Path, required=True)
    parser.add_argument('--game-root', type=Path, required=True)
    parser.add_argument('--dagor-root', type=Path, required=True)
    parser.add_argument('--dae-root', type=Path, required=True)
    parser.add_argument('--oodle-library', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.evidence.exists():
        raise ValueError('use fresh output and evidence directories')
    args.output.mkdir(parents=True)
    args.evidence.mkdir(parents=True)
    report = dict(schema='wt_ground_microdetails_preparation_v1', status='preparing',
                  script_sha256=sha(Path(__file__)), command=sys.argv, checks=[], textures=[])

    def check(name, ok, **detail):
        report['checks'].append(dict(name=name, passed=bool(ok), **detail))
        if not ok:
            raise ValueError(name)

    try:
        original = json.loads(args.source_record.read_text(encoding='utf-8-sig'))
        source_config = original['source_config']
        scene_path = args.map_package / 'scene-source.json'
        scene_sha = sha(scene_path)
        scene = json.loads(scene_path.read_text(encoding='utf-8-sig'))
        check('source_map_identity', original['source_level_sha256'] == scene['source_sha256'] == LEVEL_SHA)
        check('verified_level_config_bytes', sha(args.level_json) == source_config['sha256'] == source_config['entry']['output_sha256'])
        archive = args.game_root / 'aces.vromfs.bin'
        check('original_archive_binding', sha(archive) == source_config['archive']['sha256'])
        block, slots, uv_scale = extract_slots(json.loads(args.level_json.read_text(encoding='utf-8-sig')))
        check('source_slots_and_duplicate', len(slots) == 12 and len(set(EXPECTED)) == 11 and slots[4]['name'] == slots[11]['name'])
        commit = subprocess.check_output(['git', '-C', str(args.dagor_root), 'rev-parse', 'HEAD'], text=True).strip()
        check('pinned_public_source', commit == OFFICIAL_COMMIT)
        references = []
        for relative, details in REFERENCES.items():
            path = args.dagor_root / relative
            blob = subprocess.check_output(['git', '-C', str(args.dagor_root), 'show', commit + ':' + relative])
            check('unmodified_public_source:' + relative, path.read_bytes().replace(b'\r\n', b'\n') == blob.replace(b'\r\n', b'\n'))
            references.append(dict(path=relative, sha256=sha(path), **details))
        report.update(source_level_sha256=LEVEL_SHA, scene_source_sha256=scene_sha,
                      source_config_sha256=sha(args.level_json), source_record_sha256=sha(args.source_record),
                      archive_sha256=sha(archive), source_entry=source_config['entry'], official_commit=commit,
                      official_references=references, source_micro_details=block)
        from dagor_codec import configure_oodle
        from export_wt_textures import decode_texture
        from PIL import Image
        configure_oodle(args.oodle_library)
        sys.path.insert(0, str(args.dae_root.resolve()))
        from parse.material import DDSxTexturePack2
        import util.log as log
        log.log = lambda *unused, **kwargs: None
        needed = set(EXPECTED)
        best, candidates = {}, {name: [] for name in sorted(needed)}
        with (args.evidence / 'decoder.log').open('w', encoding='utf-8') as out, contextlib.redirect_stdout(out):
            roots = [args.game_root / x for x in ('content/base/res', 'content.hq/hq_tex/res', 'content.hq/uhq_environment/res')]
            for pack_path in sorted({p for root in roots for p in root.glob('*.dxp.bin')}):
                for texture in DDSxTexturePack2(str(pack_path)).getPackedFiles():
                    if texture.name not in needed:
                        continue
                    candidates[texture.name].append(dict(pack=pack_path.relative_to(args.game_root).as_posix(),
                        offset=texture.offset, bytes=texture.size, pixel_count=texture.getPixelCnt()))
                    if texture.name not in best or texture.getPixelCnt() > best[texture.name].getPixelCnt():
                        best[texture.name] = texture
            check('all_original_textures_found', set(best) == needed)
            (args.output / 'textures').mkdir()
            pack_hashes = {}
            for name, texture in sorted(best.items()):
                image, floats = decode_texture(texture)
                check('original_rgba8:' + name, image is not None and floats is None and image.mode == 'RGBA')
                raw = image.tobytes()
                path = args.output / 'textures' / (name + '.png')
                image.save(path)
                with Image.open(path) as reopened:
                    check('decoded_rgba_bytes_unchanged:' + name,
                          reopened.mode == image.mode and reopened.size == image.size and reopened.tobytes() == raw)
                pack_path = Path(texture.filePath)
                with pack_path.open('rb') as packed_file:
                    packed_file.seek(texture.offset)
                    packed = packed_file.read(texture.size)
                check('complete_original_payload:' + name, len(packed) == texture.size)
                relative_pack = pack_path.relative_to(args.game_root).as_posix()
                if relative_pack not in pack_hashes:
                    pack_hashes[relative_pack] = sha(pack_path)
                header = texture._DDSx__header
                record = dict(name=name, file=path.relative_to(args.output).as_posix(), sha256=sha(path),
                    size=list(image.size), mode=image.mode, rgba_sha256=hashlib.sha256(raw).hexdigest(),
                    source_pack=relative_pack, source_pack_sha256=pack_hashes[relative_pack],
                    source_offset=texture.offset, source_bytes=texture.size,
                    source_packed_sha256=hashlib.sha256(packed).hexdigest(),
                    source_format=header.d3dformat.decode('ascii'), source_mip_levels=header.levels)
                report['textures'].append(record)
        report['texture_candidates'] = candidates
        dimensions = sorted({tuple(t['size']) for t in report['textures']})
        check('uniform_original_slice_dimensions', len(dimensions) == 1, dimensions=dimensions)
        check('native_source_resolution', dimensions == [(512, 512)], dimensions=dimensions)
        textures = {x['name']: x for x in report['textures']}
        for slot in slots:
            texture = textures[slot['name']]
            slot.update(texture_file=texture['file'], texture_sha256=texture['sha256'],
                        rgba_sha256=texture['rgba_sha256'], size=texture['size'])
        check('duplicate_slot_same_pixels', slots[4]['rgba_sha256'] == slots[11]['rgba_sha256'])
        check('scene_source_unchanged', sha(scene_path) == scene_sha)
        check('source_config_unchanged', sha(args.level_json) == source_config['sha256'])
        (args.output / 'source').mkdir()
        shutil.copyfile(args.level_json, args.output / 'source' / 'level.json')
        source_binding = dict(schema='wt_ground_microdetails_source_binding_v1', source_level_sha256=LEVEL_SHA,
                              original_config=source_config, source_record_sha256=sha(args.source_record))
        write_json(args.output / 'source' / 'provenance.json', source_binding)
        count_denominator = 255 // (len(slots) - 1)
        count_scale = struct.unpack('<f', struct.pack('<f', 255.0 / count_denominator))[0]
        scope = dict(kind='source_backed_ue_post_rvt_microdetail_adaptation',
            original_assets_and_slot_parameters=True,
            public_full_land_apply_helper_available=True,
            public_land_aces_apply_call_available=False,
            interpretation='Apply the public land microdetail helper after the UE RVT composition using the original level array and IDs.',
            renderer_mapping='UE RVT ID transport, compression, interpolation and normal combination are adapter behavior.')
        report.update(status='passed', slot_count=len(slots), unique_texture_count=len(textures),
                      dimensions=list(dimensions[0]), uv_scale_per_m=uv_scale,
                      id_count_scale=count_scale, id_integer_denominator=count_denominator,
                      adaptation_scope=scope, original_image_resampling=False,
                      original_image_pixel_changes=0, native_validation='pending_parent_material_and_runtime_gates')
        write_json(args.output / 'proof.json', report)
        definition = dict(schema='wt_ground_microdetails_v1', status='prepared',
            source_level_sha256=LEVEL_SHA, source_config_sha256=sha(args.level_json),
            source_config_file='source/level.json', source_binding_file='source/provenance.json',
            source_binding_sha256=sha(args.output / 'source' / 'provenance.json'),
            slot_count=len(slots), unique_texture_count=len(textures), slice_size=list(dimensions[0]),
            texture_srgb=False, channels='RGBA8 original decoded bytes; R diffuse/smoothness, A/G normal XY, B reflectance/AO',
            uv_scale_per_m=uv_scale, source_uv_expression='worldPosXZ * land_micro_details_uv_scale',
            ue_uv_expression='AbsoluteWorldPosition.xy * 0.01 * uv_scale_per_m',
            source_uv_rotation_degrees=0, id_count_scale=count_scale,
            id_count_scale_exact_ratio=[255, count_denominator],
            id_decode_expression='floor(source_id_unorm * id_count_scale + 0.5)',
            default_params=PARAM_DEFAULTS, slots=slots,
            sampling_scope=dict(public_default='correct_bilinear',
                correct_bilinear='Four gathered IDs rounded independently; sample and bilinearly blend their four array values.',
                other_quality='Blend floor and next array slices using fractional ID.',
                simplified_land='Round the single ID and multiply UV gradients by four.'),
            adaptation_scope=scope, proof_file='proof.json', proof_sha256=sha(args.output / 'proof.json'))
        write_json(args.output / 'definition.json', definition)
        report['definition_sha256'] = sha(args.output / 'definition.json')
        report['proof_sha256'] = sha(args.output / 'proof.json')
    except Exception as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        write_json(args.evidence / 'verification.json', report)
    summary = {key: report[key] for key in ('status', 'source_level_sha256', 'source_config_sha256',
        'slot_count', 'unique_texture_count', 'dimensions', 'uv_scale_per_m', 'id_count_scale',
        'original_image_resampling', 'original_image_pixel_changes', 'definition_sha256', 'proof_sha256')}
    summary.update(verification_sha256=sha(args.evidence / 'verification.json'),
                   checks_passed=sum(x['passed'] for x in report['checks']),
                   native_validation=report['native_validation'])
    write_json(args.evidence / 'summary.json', summary)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
