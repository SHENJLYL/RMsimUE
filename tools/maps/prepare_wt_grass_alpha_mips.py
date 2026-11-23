"""Restore every original grass alpha mip as a linear R8 DDS companion package.

Run after prepare_wt_foliage_enhancement.py. --publish changes only the foliage
alpha clip/provenance and adds the source-alpha-mips companion; existing PNGs,
models, weights, colors, normals and native assets are untouched.
"""
from __future__ import annotations
import argparse
import datetime
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import sys
import numpy as np
from PIL import Image, __version__ as pillow_version


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def require(value, message):
    if not value:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--source-foliage', type=Path)
    parser.add_argument('--source-small-grass', type=Path)
    parser.add_argument('--oodle-library', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    package = (args.package or repo/'assets/maps/wt_vlaanderen').resolve(strict=True)
    base = package/'foliage-enhanced'
    manifest = base/'foliage.json'
    original = manifest.read_bytes()
    original_sha = hashlib.sha256(original).hexdigest()
    foliage = json.loads(original)
    require('alpha_mip_adapter' not in foliage, 'start from the pre-alpha-chain foliage baseline')
    require(len(foliage['models']) == 10 and len(foliage['layers']) == 16, 'unexpected foliage inventory')
    source_old = args.source_foliage or repo/'artifacts/maps/wt-refine-20261118/source-baseline/foliage-source.json'
    source_small = args.source_small_grass or repo/'artifacts/maps/wt-refine-20261118/small-grass-source-02-visual/small-grass-source.json'
    old, small = read(source_old), read(source_small)
    specs = {Path(model['material']['alpha_texture']).stem: model for model in foliage['models']}
    wanted = {row['name']: dict(row, pack=row['source']) for row in old['textures'] if row['name'] in specs}
    wanted.update({row['name']: dict(row, pack=row['source_pack']) for row in small['textures'] if row['name'] in specs})
    require(len(specs) == len(wanted) == 10, 'original source records must cover all ten alpha textures')
    args.output.mkdir(parents=True, exist_ok=False)
    companion = args.output/'source-alpha-mips'
    companion.mkdir()
    (companion/'textures').mkdir()
    (args.output/'source-dds').mkdir()
    (companion/'foliage-before.json').write_bytes(original)
    shutil.copyfile(source_old, companion/'original-foliage-source.json')
    shutil.copyfile(source_small, companion/'original-small-grass-source.json')
    sys.path[:0] = [str(repo/'tools/unreal'), str(repo/'tools/maps'), str(repo/'third_party/dagor_asset_explorer/src/dae')]
    from wt_grass_alpha_mips import write_r8_dds, parse_r8_dds, validate_alpha_mips, SCHEMA, ADAPTER_SCHEMA
    import util.log as log
    log.log = lambda *a, **k: None
    from parse.material import DDSxTexturePack2
    from dagor_codec import configure_oodle
    configure_oodle(args.oodle_library)
    report = dict(schema='wt_grass_alpha_mip_preparation_v1', status='running',
        started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        preparer_sha256=sha(__file__), helper_sha256=sha(repo/'tools/unreal/wt_grass_alpha_mips.py'),
        baseline_definition_sha256=original_sha, source_level_sha256=foliage['source_level_sha256'],
        source_records_sha256={'original_foliage': sha(source_old), 'small_grass': sha(source_small)},
        oodle_library_sha256=sha(args.oodle_library), pillow_version=pillow_version,
        native_run=False, published=False, textures=[])
    proof = dict(schema=SCHEMA, status='running', source_level_sha256=foliage['source_level_sha256'],
        baseline_definition_file='foliage-before.json', baseline_definition_sha256=original_sha,
        source_records=[dict(file='original-foliage-source.json', sha256=sha(source_old)),
                        dict(file='original-small-grass-source.json', sha256=sha(source_small))],
        clip_value=.5, source_format='BC4_UNORM (ATI1)', output_format='DXGI_FORMAT_R8_UNORM', channel='R',
        source_decoding='Pillow BC4 decode, original byte order and every authored mip retained; no filtering or gamma conversion',
        mip_policy='LeaveExistingMips; no alpha-coverage rescaling; no resizing or regeneration',
        platform_build='existing TC_VECTOR_DISPLACEMENTMAP: uncompressed BGRA8, source R sampled; no second BC4 encoding',
        clip_provenance={'small_grass': 'fixed grass_generate.dshl:385 source opacity minimum 0.5; clip(alpha-opacity) at 785-793',
                         'original_random_grass': 'UE cutout calibration at 0.5, supported by original authored per-mip coverage; closed shader threshold not asserted',
                         'distance_fade': 'existing UE per-instance cull fade retained; source varying distance opacity is not reproduced'},
        unchanged=['original PNG bytes','models and bounds','weight maps and hard-surface exclusions','density and scales',
                   'source RGB endpoints and gamma','source normals and transmission','scene manifest','native assets'],
        textures=[])
    failure = None
    try:
        for pack_name in sorted({row['pack'] for row in wanted.values()}):
            pack = DDSxTexturePack2(pack_name)
            for texture in pack.getPackedFiles():
                if texture.name not in wanted or wanted[texture.name]['pack'] != pack_name:
                    continue
                source, model = wanted[texture.name], specs[texture.name]
                header = texture._DDSx__header
                require(header.d3dformat == b'ATI1' and header.flags & 0x8000,
                        'source alpha is not original linear BC4: '+texture.name)
                width, height = int(header.h), int(header.w)
                require(header.levels == max(width,height).bit_length(), 'original alpha mip chain is incomplete')
                with Path(pack_name).open('rb') as stream:
                    stream.seek(texture.offset)
                    payload = stream.read(texture.size)
                require(len(payload) == texture.size, 'source packed payload is truncated')
                packed_sha = hashlib.sha256(payload).hexdigest()
                for key, actual in [('source_offset',texture.offset),('source_bytes',texture.size),
                                    ('source_packed_sha256',packed_sha),('offset',texture.offset),('packed_bytes',texture.size)]:
                    if key in source:
                        require(source[key] == actual, 'original source payload binding differs: '+key)
                dds = texture.getDDS()
                require(dds[:4] == b'DDS ' and len(dds) >= 128 and dds[84:88] == b'ATI1', 'unexpected original BC4 DDS')
                source_dds = args.output/'source-dds'/(texture.name+'.dds')
                source_dds.write_bytes(dds)
                offset, pixels_by_mip, levels = 128, [], []
                original_top = regenerated = None
                for level in range(header.levels):
                    w, h = max(1,width>>level), max(1,height>>level)
                    size = max(1,(w+3)//4)*max(1,(h+3)//4)*8
                    require(offset+size <= len(dds), 'original BC4 mip is truncated')
                    single = bytearray(dds[:128])
                    struct.pack_into('<I',single,12,h)
                    struct.pack_into('<I',single,16,w)
                    struct.pack_into('<I',single,28,1)
                    image = Image.open(io.BytesIO(bytes(single)+dds[offset:offset+size])).convert('L')
                    data = np.asarray(image)
                    raw = data.tobytes()
                    pixels_by_mip.append(raw)
                    if level == 0:
                        original_top, regenerated = data.copy(), image.copy()
                    else:
                        regenerated = regenerated.resize((w,h), Image.Resampling.BOX)
                    box = np.asarray(regenerated)
                    levels.append(dict(level=level,size=[w,h],bytes=len(raw),pixel_sha256=hashlib.sha256(raw).hexdigest(),
                        source_bc4_bytes=size,source_bc4_sha256=hashlib.sha256(dds[offset:offset+size]).hexdigest(),
                        source_coverage_at_1_3=float(np.mean(data>=85)),source_coverage_at_0_5=float(np.mean(data>=128)),
                        diagnostic_box_coverage_at_0_5=float(np.mean(box>=128))))
                    offset += size
                material = model['material']
                prepared = base/material['alpha_texture']
                require(sha(prepared) == material['alpha_texture_sha256'], 'original prepared PNG SHA differs')
                require(np.array_equal(original_top,np.asarray(Image.open(prepared).convert('L'))), 'original mip0 differs from prepared alpha PNG')
                require(offset == len(dds), 'original BC4 DDS has unaccounted bytes')
                raw_dds = write_r8_dds(width,height,pixels_by_mip)
                target = companion/'textures'/(model['id']+'_Alpha.dds')
                target.write_bytes(raw_dds)
                readback = parse_r8_dds(raw_dds)
                require(all(got['pixels'] == wanted_pixels for got,wanted_pixels in zip(readback['mips'],pixels_by_mip)),
                        'R8 DDS roundtrip changed pixels')
                address_u, address_v = (header.flags & 15) or 1, ((header.flags>>4)&15) or 1
                require(address_u == address_v == 1, 'unexpected original alpha sampler address; review explicitly')
                ue_address = material.get('texture_address','wrap')
                row = dict(model_id=model['id'],name=texture.name,size=[width,height],source_levels=header.levels,
                    source_pack=pack_name,payload_offset=texture.offset,payload_bytes=texture.size,payload_sha256=packed_sha,
                    source_ddsx_header_hex=header.getBin().hex(),source_ddsx_flags=header.flags,source_gamma_equal_one=True,
                    source_address_u='wrap',source_address_v='wrap',ue_address_u=ue_address,ue_address_v=ue_address,
                    address_provenance='original wrap' if ue_address=='wrap' else 'existing UE single-atlas card clamp; original source is wrap',
                    prepared_png_sha256=sha(prepared),top_mip_exact_equals_prepared_png=True,all_source_dds_mip_bytes_accounted=True,
                    source_bc4_dds_sha256=sha(source_dds),dds_file=target.relative_to(companion).as_posix(),
                    dds_sha256=sha(target),channel='R',mips=levels)
                proof['textures'].append(row)
        proof['textures'].sort(key=lambda row:row['model_id'])
        require(len(proof['textures']) == 10, 'did not restore all ten source alpha textures')
        proof['total_mips'] = sum(row['source_levels'] for row in proof['textures'])
        proof['total_r8_pixel_bytes'] = sum(mip['bytes'] for row in proof['textures'] for mip in row['mips'])
        proof['status'] = 'passed'
        proof_path = companion/'definition.json'
        save(proof_path,proof)
        candidate = dict(foliage)
        candidate['alpha_clip_value'] = .5
        candidate['alpha_clip_provenance'] = proof['clip_provenance']
        candidate['alpha_mip_adapter'] = dict(schema=ADAPTER_SCHEMA,definition_file='source-alpha-mips/definition.json',
            definition_sha256=sha(proof_path),source_channel='R',mip_policy='LeaveExistingMips',srgb=False)
        candidate_path = args.output/'foliage.candidate.json'
        save(candidate_path,candidate)
        report.update(status='passed',textures=proof['textures'],alpha_mip_definition_sha256=sha(proof_path),
            candidate_definition_sha256=sha(candidate_path),total_mips=proof['total_mips'],
            total_r8_pixel_bytes=proof['total_r8_pixel_bytes'],
            diagnostic_box_scope='mip0 BOX is a diagnostic comparison, not UE cooked texture readback',
            production_modified_fields=['alpha_clip_value','alpha_clip_provenance','alpha_mip_adapter'])
        if args.publish:
            require(sha(manifest) == original_sha, 'active foliage changed before publication')
            destination = base/'source-alpha-mips'
            require(not destination.exists(), 'alpha mip companion destination already exists')
            shutil.copytree(companion,destination)
            try:
                validate_alpha_mips(base,candidate)
                temporary = manifest.with_name('foliage.alpha-mips.tmp.json')
                require(not temporary.exists(), 'publication temporary file already exists')
                temporary.write_bytes(candidate_path.read_bytes())
                require(sha(manifest) == original_sha, 'active foliage changed during publication')
                temporary.replace(manifest)
                report.update(published=True,published_definition_sha256=sha(manifest))
            except Exception:
                # Keep the isolated companion for diagnosis; original definition stays authoritative.
                raise
    except Exception as error:
        failure = error
        report.update(status='failed',error=repr(error))
    finally:
        report['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(args.output/'verification.json',report)
    print(json.dumps({key:report.get(key) for key in ('status','published','total_mips','total_r8_pixel_bytes',
        'baseline_definition_sha256','candidate_definition_sha256','alpha_mip_definition_sha256','error')}))
    if failure:
        raise failure


if __name__ == '__main__':
    main()
