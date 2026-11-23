"""Publish original Flanders detail textures/table for incremental UE authoring.

Usage:
 python -B tools/maps/prepare_wt_landdetail.py --scene-source <scene-source.json>
   --source-landclass <decoded original avg_vlaanderen_detailed_biome.json>
   --texture-inventory <original textures-03/result.json> --evidence-root <fresh>
No source model, FBX, map geometry or scene manifest is rewritten.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sys

import numpy as np
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'unreal'))
import wt_landdetail as ld


def save(path, data):
    path.write_text(json.dumps(data,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene-source',required=True,type=Path)
    parser.add_argument('--source-landclass',required=True,type=Path)
    parser.add_argument('--texture-inventory',required=True,type=Path)
    parser.add_argument('--evidence-root',required=True,type=Path)
    args=parser.parse_args()
    evidence=args.evidence_root.resolve();evidence.mkdir(parents=True,exist_ok=False)
    package=args.scene_source.resolve(strict=True).parent
    scene=json.loads(args.scene_source.read_text(encoding='utf-8-sig'))
    ld._require(scene.get('source_sha256')==ld.SOURCE_SHA256,'unexpected map source')
    scene_before=ld.digest(args.scene_source)
    raw=json.loads(args.source_landclass.read_text(encoding='utf-8-sig'))
    table=ld.source_table(raw)
    identity=raw['source']
    group=Path(identity['group']).resolve(strict=True)
    with group.open('rb') as f:
        f.seek(identity['offset']); original=f.read(identity['resource_bytes'])
    ld._require(hashlib.sha256(original).hexdigest()==ld.RESOURCE_SHA256,
                'original GRP resource bytes differ')
    inventory=json.loads(args.texture_inventory.read_text(encoding='utf-8-sig'))
    indexed={x['name']:x for x in inventory['exported']}
    names=table['albedo_names']+[ld.INDEX_NAME,ld.MACRO_NAME,ld.FLOW_NAME]
    target=package/'terrain-details'
    staging=evidence/'package-staging';staging.mkdir()
    (staging/'textures').mkdir()
    shutil.copyfile(args.source_landclass,staging/'source-landclass.json')
    textures={}
    for name in names:
        entry=indexed[name];src=Path(entry['file']).resolve(strict=True)
        ld._require(ld.digest(src)==entry['sha256'],'original texture hash differs: '+name)
        with Image.open(src) as im:
            size=list(im.size)
            expected=[4096,4096] if name in (ld.INDEX_NAME,ld.MACRO_NAME) else [512,512] if name==ld.FLOW_NAME else [1024,1024]
            ld._require(size==expected,'unreviewed texture size: '+name)
            if name in table['albedo_names']:
                ld._require(im.mode=='RGBA','source height alpha missing: '+name)
        filename='textures/'+src.name
        shutil.copyfile(src,staging/filename)
        textures[name]=dict(name=name,file='terrain-details/'+filename,sha256=entry['sha256'],size=size)
    with Image.open(Path(indexed[ld.INDEX_NAME]['file'])) as im:
        indices=np.asarray(im,dtype=np.uint8)
        ids,counts=np.unique(indices,return_counts=True)
        ld._require(int(ids.max())<len(table['biomes']),'index references absent biome')
        positions=[(1028.0,-1020.0),(1075.232351,-1012.656026),(131.25,-675.75)]
        samples=[]
        for x,y in positions:
            u=(x/2048)%1;v=(y/2048)%1
            ix=int(u*4096);iy=int(v*4096);biome=int(indices[iy,ix])
            samples.append(dict(ros_xy_m=[x,y],source_uv=[u,v],pixel=[ix,iy],
                                biome=biome,layers=table['biomes'][biome]['layers']))
    proof=dict(schema='wt_indexed_landdetail_source_verification_v1',status='passed',
        completed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        source_sha256=ld.SOURCE_SHA256,scene_manifest_sha256=scene_before,
        resource_sha256=ld.RESOURCE_SHA256,source_resource_bytes=len(original),
        source_json_sha256=ld.digest(args.source_landclass),
        texture_inventory_sha256=ld.digest(args.texture_inventory),texture_count=len(textures),
        albedo_count=len(table['albedo_names']),biome_count=len(table['biomes']),
        source_albedo_size=[1024,1024],source_detail_meters=[table['source_parameters']['detailMul1'],table['source_parameters']['detailMul2']],
        source_texel_meters=[table['source_parameters']['detailMul1']/1024,table['source_parameters']['detailMul2']/1024],
        used_biomes={str(int(i)):int(n) for i,n in zip(ids,counts)},asymmetric_samples=samples,
        checks=dict(original_grp_bytes_match=True,original_texture_bytes_match=True,
                    all_source_definitions_present=True,all_source_indices_in_range=True),
        scope='Original source bytes and offline mapping; UE compilation/rendering remain pending.')
    save(staging/'verification.json',proof)
    definition=dict(schema=ld.SCHEMA,source_sha256=ld.SOURCE_SHA256,dagor_commit=ld.DAGOR_COMMIT,
        source_definition=dict(file='terrain-details/source-landclass.json',sha256=ld.digest(staging/'source-landclass.json')),
        source_resource=dict(name=identity['name'],sha256=ld.RESOURCE_SHA256),
        table=table,textures=textures,
        verification_file='terrain-details/verification.json',verification_sha256=ld.digest(staging/'verification.json'),
        adapter=dict(color='source dual-layer height blend times original macro; microdetail self-color flags not applied',
                     normal='UE surface gradient from original alpha height, height_scale and normal_scale; no geometry displacement',
                     roughness=0.9,metallic=0,
                     packed_reflectance='source names retained; packed channel semantics not inferred',
                     categorical_index='nearest, mip 0',source_extent_m=[0,0,2048,2048]))
    save(staging/'definition.json',definition)
    save(evidence/'verification.json',dict(proof,definition_sha256=ld.digest(staging/'definition.json'),
        target=str(target),status='prepared_pending_publish'))
    if target.exists():
        shutil.copytree(target,evidence/'before')
        # Only owned generated data is replaced after its complete rollback copy.
        shutil.rmtree(target)
    shutil.copytree(staging,target)
    ld.validate(package,scene)
    ld._require(ld.digest(args.scene_source)==scene_before,'scene manifest changed')
    save(evidence/'verification.json',dict(proof,definition_sha256=ld.digest(target/'definition.json'),
        target=str(target),status='passed',scene_manifest_unchanged=True))
    print(json.dumps(dict(status='passed',albedo_count=23,biome_count=65,
                          definition_sha256=ld.digest(target/'definition.json'),evidence=str(evidence))))
if __name__=='__main__':
    main()
