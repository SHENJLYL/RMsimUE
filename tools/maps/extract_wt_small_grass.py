"""Extract three original Flanders grass mask/alpha/normal sets for the UE adapter.

The source configuration and game archives are read only. Geometry/distribution
adaptation is performed by separate preparation steps and is explicitly recorded.
"""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import sys

from extract_wt_foliage import sha, one, required_child, color_records

TYPES = ('generic_grass_small_a', 'generic_grass_tall_a', 'generic_grass_tall_b')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--game-root', type=Path, required=True)
    p.add_argument('--dae-root', type=Path, required=True)
    p.add_argument('--oodle-library', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        raise ValueError('use a fresh output directory')
    source = json.loads(a.source.read_text(encoding='utf-8-sig'))
    small = source['small_grass_source']
    types = {x['name']: x for x in required_child(small, 'grass_types')['children']}
    selected = {name: types[name] for name in TYPES}
    suffix = one(small, 'alphaSuffix')
    if suffix != '_a':
        raise ValueError('source paired-alpha naming changed')
    needed = {one(t, 'diffuse') + s for t in selected.values() for s in ('', suffix)}
    needed.update(one(t, 'normal') for t in selected.values())
    a.output.mkdir(parents=True)
    (a.output/'textures').mkdir()
    report = dict(schema='wt_small_grass_source_v1', version=2, status='preparing',
        source_level_sha256=source['source_level_sha256'], source_file_sha256=sha(a.source),
        source_configuration=small, models=[], textures=[])
    try:
        from dagor_codec import configure_oodle
        from export_wt_textures import decode_texture
        configure_oodle(a.oodle_library)
        sys.path.insert(0, str(a.dae_root.resolve()))
        from parse.material import DDSxTexturePack2
        import util.log as log
        log.log = lambda *args, **kwargs: None
        best = {}
        with (a.output/'decoder.log').open('w', encoding='utf-8') as f, contextlib.redirect_stdout(f):
            roots = [a.game_root/'content/base/res', a.game_root/'content.hq/hq_tex/res',
                     a.game_root/'content.hq/uhq_environment/res']
            for path in sorted({p for root in roots for p in root.glob('*.dxp.bin')}):
                pack = DDSxTexturePack2(str(path))
                for texture in pack.getPackedFiles():
                    if texture.name in needed and (texture.name not in best or
                            texture.getPixelCnt() > best[texture.name].getPixelCnt()):
                        best[texture.name] = texture
            if set(best) != needed:
                raise ValueError('missing original small grass textures: '+repr(needed-set(best)))
            for name, texture in sorted(best.items()):
                image, floats = decode_texture(texture)
                if image is None or floats is not None:
                    raise ValueError('unexpected source texture format')
                path = a.output/'textures'/(name+'.png')
                image.save(path)
                from PIL import Image
                with Image.open(path) as reopened:
                    if reopened.mode != image.mode or reopened.size != image.size or reopened.tobytes() != image.tobytes():
                        raise ValueError('source texture pixels changed during PNG export')
                header = texture._DDSx__header
                if not header.flags & 0x8000:
                    raise ValueError('source grass mask/alpha/normal must declare DDSx GAMMA_EQ_1')
                with Path(texture.filePath).open('rb') as f:
                    f.seek(texture.offset)
                    packed = f.read(texture.size)
                report['textures'].append(dict(name=name, file=path.relative_to(a.output).as_posix(),
                    sha256=sha(path), size=list(image.size), mode=image.mode,
                    source_pack=str(texture.filePath), source_offset=texture.offset,
                    source_bytes=texture.size, source_packed_sha256=hashlib.sha256(packed).hexdigest(),
                    source_ddsx_flags=int(header.flags), source_gamma_equal_one=True,
                    source_format=header.d3dformat.decode('ascii'),
                    decoded_pixel_sha256=hashlib.sha256(image.tobytes()).hexdigest(),
                    decoded_pixel_change_count=0))
        for name, spec in selected.items():
            report['models'].append(dict(id=name, parameters=spec['params'], source_colors=color_records(spec),
                texture=one(spec, 'diffuse'), alpha_texture=one(spec, 'diffuse')+suffix,
                normal_texture=one(spec, 'normal'),
                normal_semantics=dict(x='A', y='G', z='sqrt(saturate(1-dot(AG*2-1,AG*2-1)))',
                    translucency='B', smoothness='R', source='grass_generate.dshl:821-826'),
                geometry='UE crossed-card adaptation; original small-grass vertices are generated by source GPU shader'))
        report['status'] = 'passed'
    except Exception as e:
        report.update(status='failed', error=repr(e))
        raise
    finally:
        (a.output/'small-grass-source.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(status=report['status'], texture_count=len(report['textures']),
        report_sha256=sha(a.output/'small-grass-source.json')), indent=2))


if __name__ == '__main__':
    main()
