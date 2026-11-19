"""Prepare the original 23 Flanders landclass reflectance textures and source contract.

The companion preserves cooked RGBA pixels. It binds the confirmed BARG export
rule and the original 65-biome mapping without changing terrain-details, grass,
or the scene manifest. Run offline; no Unreal process is used.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import numpy as np
from PIL import Image

COMMIT = '9639942de5a8e5b01d91977f60ed82cda22d5f41'
SWIZZLE_SHA = 'f3fde1e24431dd191593fc737444ece16c02f776494da8e675646a61a3761c77'
AUDIT_SHA = '8894fe4eeb64b0ad49a7902104a2c52ec537a6a128406eadf7f2d7aa76e51e8d'


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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,required=True)
    ap.add_argument('--package',type=Path,required=True)
    ap.add_argument('--source-audit',type=Path,required=True)
    ap.add_argument('--swizzle-proof',type=Path,required=True)
    ap.add_argument('--oodle-library',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--evidence',type=Path,required=True)
    args = ap.parse_args()
    require(not args.output.exists() and not args.evidence.exists(), 'use fresh output/evidence directories')
    repo, package = args.repo.resolve(strict=True), args.package.resolve(strict=True)
    require(sha(args.source_audit)==AUDIT_SHA and sha(args.swizzle_proof)==SWIZZLE_SHA,
            'reviewed source/channel proof changed')
    original_file=package/'terrain-details/definition.json'
    original=read(original_file)
    raw_file=package/original['source_definition']['file']
    require(sha(raw_file)==original['source_definition']['sha256'], 'raw landclass resource changed')
    micro_file=package/'ground-microdetails/definition.json';micro=read(micro_file)
    require(micro['slot_count']==12 and micro['texture_srgb'] is False, 'source microdetail contract changed')
    audit,swizzle=read(args.source_audit),read(args.swizzle_proof)
    require(swizzle['status']=='source_verified' and swizzle['official_commit']==COMMIT and
            'swizzleARGB:t="BARG"' in swizzle['ground_rule']['contents'], 'BARG consumer proof is incomplete')
    require(audit['source_definition_sha256']==sha(raw_file) and audit['source_texture_count']==23 and
            audit['all_linear'] is True, 'reflectance inventory source differs')
    table=original['table'];definitions=table['definitions']
    names=sorted({d['reflectance'] for d in definitions.values() if d.get('reflectance')})
    require(len(names)==23 and {r['name'] for r in audit['textures']}==set(names), '23 original reflectance names differ')
    pairs=[]
    for i,albedo in enumerate(table['albedo_names']):
        choices={d['reflectance'] for d in definitions.values() if d.get('albedo')==albedo}
        require(len(choices)==1, 'one albedo maps to multiple reflectance textures; preserve independent table explicitly')
        reflectance=choices.pop()
        pairs.append(dict(albedo_slice=i,albedo=albedo,reflectance_slice=names.index(reflectance),reflectance=reflectance))
    require(len({p['reflectance_slice'] for p in pairs})==23, 'expected original bijective 23-pair source mapping')
    biome_rows=[dict(id=b['id'],reflectance_slices=[names.index(l['reflectance']) for l in b['layers']],
                    grass_microdetail_removal_bits=b['grass_microdetail_removal_bits']) for b in table['biomes']]
    args.output.mkdir(parents=True);(args.output/'textures').mkdir();(args.output/'source').mkdir()
    args.evidence.mkdir(parents=True)
    for source,name in [(args.source_audit,'reflectance-source-audit.json'),(args.swizzle_proof,'ground-swizzle-consumer.json'),
                        (original_file,'landdetail-definition.json'),(raw_file,'source-landclass.json'),(micro_file,'microdetail-definition.json')]:
        shutil.copyfile(source,args.output/'source'/name)
    references=[]
    for path,lines,purpose in [
        ('prog/daNetGame/shaders/make_clipmap.dshl','652-679,803-834,1092-1104','shared final weights; winning micro ID; per-neighbor selfcolor; grass-mask-only removal'),
        ('prog/gameLibs/landMesh/shaders/land_micro_detail.dshl','109-120,170-194','interpolated ID parameter lookup and coloring'),
        ('prog/tools/sceneTools/assetExp/exporters/texExp.cpp','2377-2407,1796-1803','BARG cooked-channel consumer')]:
        raw=subprocess.check_output(['git','-C',str(repo/'third_party/dagor_engine'),'show',COMMIT+':'+path])
        target=args.output/'source'/Path(path).name;target.write_bytes(raw)
        references.append(dict(path=path,lines=lines,purpose=purpose,file=target.relative_to(args.output).as_posix(),sha256=sha(target)))
    sys.path[:0]=[str(repo/'tools/maps'),str(repo/'third_party/dagor_asset_explorer/src/dae')]
    import util.log as log
    log.log=lambda *a,**k:None
    from parse.material import DDSxTexturePack2
    from dagor_codec import configure_oodle
    from export_wt_textures import decode_texture
    configure_oodle(args.oodle_library)
    rows={r['name']:r for r in audit['textures']}
    textures={};report=dict(schema='wt_lc_reflectance_preparation_v1',status='running',checks=[],native_run=False,
        script_sha256=sha(__file__),source_landdetail_sha256=sha(original_file),source_landclass_sha256=sha(raw_file),
        source_audit_sha256=sha(args.source_audit),swizzle_proof_sha256=sha(args.swizzle_proof))
    failure=None
    try:
        for pack_name in sorted({r['original_pack'] for r in rows.values()}):
            pack=DDSxTexturePack2(pack_name)
            for texture in pack.getPackedFiles():
                if texture.name not in rows or rows[texture.name]['original_pack']!=pack_name:continue
                row=rows[texture.name];header=texture._DDSx__header
                require(texture.offset==row['original_payload_offset'] and header.flags==int(row['flags'],16), 'original payload/header differs')
                require(header.flags&0x8000 and ((header.flags&15) or 1)==1 and (((header.flags>>4)&15) or 1)==1,
                        'reflectance source must remain linear wrap')
                require([header.h,header.w]==[1024,1024], 'original highest mip dimensions differ')
                image,floats=decode_texture(texture)
                require(floats is None and image.mode=='RGBA' and image.size==(1024,1024),'unexpected original reflectance decode')
                source_png=Path(row['file']);require(sha(source_png)==row['sha256'],'audited original PNG changed')
                pixels=np.asarray(image)
                with Image.open(source_png) as old:
                    require(old.mode=='RGBA' and old.size==image.size and old.tobytes()==image.tobytes(), 'original highest mip pixels differ')
                target=args.output/'textures'/(texture.name+'.png');shutil.copyfile(source_png,target)
                with Path(pack_name).open('rb') as stream:
                    stream.seek(texture.offset);payload=stream.read(texture.size)
                require(len(payload)==texture.size,'original packed texture truncated')
                source_ids=np.floor(pixels[:,:,3].astype(float)/255*micro['id_count_scale']+.5).astype(int)
                counts=np.bincount(source_ids.ravel(),minlength=12)
                require(len(counts)==12,'original cooked A references unavailable source microdetail slot')
                textures[texture.name]=dict(name=texture.name,file=target.relative_to(args.output).as_posix(),sha256=sha(target),
                    size=[1024,1024],rgba_sha256=hashlib.sha256(image.tobytes()).hexdigest(),srgb=False,
                    source_pack=pack_name,source_offset=texture.offset,source_bytes=texture.size,
                    source_payload_sha256=hashlib.sha256(payload).hexdigest(),source_ddsx_header_hex=header.getBin().hex(),
                    source_flags=header.flags,source_format=header.d3dformat.decode('ascii'),source_mip_levels=header.levels,
                    source_highest_mip_exact=True,microdetail_slot_pixel_counts=counts.tolist())
        require(set(textures)==set(names),'source reflectance extraction incomplete')
        removal_bits=sorted({b['grass_microdetail_removal_bits'] for b in table['biomes']})
        active_bits=sorted({bit for bits in removal_bits for bit in range(32) if bits&(1<<bit)})
        report.update(status='passed',textures=list(textures.values()),texture_count=23,biome_count=65,
            original_png_bytes_unchanged=True,decoded_highest_mip_matches_original=True,
            removal_bit_indices=active_bits,removal_bits_overlap_level_slots=[i for i in active_bits if i<12])
        save(args.output/'proof.json',report)
        definition=dict(schema='wt_lc_reflectance_v1',status='prepared',source_sha256=original['source_sha256'],
            dagor_commit=COMMIT,landdetail_definition_sha256=sha(original_file),raw_landclass_sha256=sha(raw_file),
            microdetail_definition_sha256=sha(micro_file),swizzle_proof_file='source/ground-swizzle-consumer.json',swizzle_proof_sha256=SWIZZLE_SHA,
            source_audit_file='source/reflectance-source-audit.json',source_audit_sha256=AUDIT_SHA,
            channels={'R':'smoothness','G':'reflectance','B':'ambient_occlusion','A':'microdetail_id_unorm'},
            reflectance_names=names,albedo_to_reflectance=pairs,biomes=biome_rows,textures=textures,
            microdetail_coloring=[slot['params'][0] for slot in micro['slots']],id_count_scale=micro['id_count_scale'],
            source_references=references,proof_file='proof.json',proof_sha256=sha(args.output/'proof.json'),
            source_sampling={'UV':'same as each albedo neighbor and layer','rotation':'same four-component matrix',
                'weights':'same final normalized height/pow4 weights','selfcolor':'lerp(1,macro,lerped source slot coloring) per neighbor',
                'microdetail_neighbor_selection':'strict greater weight; first neighbor wins ties',
                'microdetail_layer_selection':'layer 0 only if weight0 > weight1; layer 1 wins ties',
                'grass_removal_bits':'source grass-mask generation only; bits24/25 absent from this level12slots'},
            ue_adapter={'filter':'SampleGrad with preclassification rotated gradients','sampler':'reuse DetailsSampler',
                'roughness':'1 - cooked R','microdetail':'cooked A selected by source winners; no weighted average of categorical IDs',
                'not_connected':['reflectance G','ambient_occlusion B'],
                'array_compression':'TC_VECTOR_DISPLACEMENTMAP linear BGRA8, preserve all decoded channels',
                'mips':'UE simple-average derived from original highest mip; full original HQ/base mip assembly not claimed',
                'max_reflectance_reads':8,'min_reflectance_reads':2,'max_combined_detail_reads':32,
                'additional_sampler_states':0,'array_full_mip_bgra8_bytes':23*sum(max(1,1024>>i)**2*4 for i in range(11))},
            native_validation='pending')
        save(args.output/'definition.json',definition)
        report['definition_sha256']=sha(args.output/'definition.json')
    except Exception as error:
        failure=error;report.update(status='failed',error=repr(error))
    finally:save(args.evidence/'verification.json',report)
    print(json.dumps({key:report.get(key) for key in ('status','texture_count','biome_count','definition_sha256','removal_bit_indices','removal_bits_overlap_level_slots','error')}))
    if failure:raise failure


if __name__=='__main__':main()
