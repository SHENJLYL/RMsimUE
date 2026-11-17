"""Resolve and decode original textures referenced by the selected map's models and landclasses."""
import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import struct
import numpy as np
from PIL import Image
from dagor_codec import configure_oodle


def decode_texture(tex):
    header=tex._DDSx__header
    kind=struct.unpack('<I',header.d3dformat)[0]
    if kind not in (21,32,50,113):
        image=Image.open(io.BytesIO(tex.getDDS()));image.load()
        return image,None
    # The legacy reader's w/h member names are reversed; DDSx stores width then height.
    width,height=header.h,header.w
    channels=1 if kind==50 else 4;dtype='<f2' if kind==113 else 'u1'
    item=np.dtype(dtype).itemsize
    data=tex.getData();offset=0
    if header.flags&0x40000:
        offset=sum(max(1,width>>i)*max(1,height>>i)*channels*item for i in range(1,header.levels))
    size=width*height*channels*item
    if offset+size>len(data):raise ValueError('raw texture mip exceeds decoded data')
    array=np.frombuffer(data[offset:offset+size],dtype=dtype).reshape(height,width,channels)
    if kind==113:
        if not np.all(np.isfinite(array)):raise ValueError('nonfinite float data texture')
        return None,array.copy()
    if kind==21:array=array[:,:,[2,1,0,3]]
    return Image.fromarray(array[:,:,0] if kind==50 else array),None


def main():
    p=argparse.ArgumentParser();p.add_argument('--index',type=Path,required=True)
    p.add_argument('--game-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--level',default='avg_vlaanderen');p.add_argument('--oodle-library',required=True)
    p.add_argument('--reuse',type=Path)
    p.add_argument('--extra-textures',type=Path,help='JSON array of additional scene/landmesh texture names')
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    configure_oodle(a.oodle_library)
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'third_party/dagor_asset_explorer/src/dae'))
    from parse.gameres import GameResDesc
    from parse.material import DDSxTexturePack2
    import util.log as log
    log.log=lambda *args,**kwargs:None
    index=json.loads(a.index.read_text(encoding='utf-8'))['resources']
    required=set();land_refs=set();textures={};scan_errors=[];failures=[];exported=[];aliases=set()
    cached={}
    if a.reuse:
        previous=json.loads(a.reuse.read_text(encoding='utf-8'))
        cached={t['name']:t for t in previous['exported']}
    with (out/'decoder.log').open('w',encoding='utf-8') as logfile,contextlib.redirect_stdout(logfile):
        desc=GameResDesc(str(a.game_root/'content/base/res/riDesc.bin'));desc.loadDataBlock()
        for name in index:
            required.update(t.split('*')[0].split('$')[0] for t in desc.getModelTextures(name) if t)
        land=a.game_root/'WarThunderCDK/develop/assets/landclasses/air_vs_ground_detailed'/a.level
        keys='texture|albedo|reflectance|indices|flowmap|grass_weight|splattingmap|detailRed|detailGreen|detailBlue|detailBlack|detail2Red|detail2Green|detail2Blue|detail2Black'
        for file in land.glob('*.land.blk'):
            text=file.read_text(encoding='utf-8')
            land_refs.update(re.findall(r'\b(?:'+keys+r')\s*:t\s*=\s*"([^"]+)"',text))
            aliases.update(re.findall(r'(?m)^\s*([A-Za-z0-9_]+)\s*\{\s*\n\s*albedo\s*:t',text))
        land_refs-=aliases
        required.update(land_refs)
        if a.extra_textures:
            required.update(name.split('*')[0].split('$')[0] for name in json.loads(a.extra_textures.read_text(encoding='utf-8')))
        roots=[a.game_root/'content/base/res',a.game_root/'content.hq/hq_tex/res',a.game_root/'content.hq/uhq_environment/res']
        paths=sorted({p for folder in roots for p in folder.glob('*.dxp.bin')})
        for path in paths:
            try:
                pack=DDSxTexturePack2(str(path))
                for tex in pack.getPackedFiles():
                    if tex.name in required or tex.name.startswith(a.level):
                        if tex.name not in textures or tex.getPixelCnt()>textures[tex.name].getPixelCnt():textures[tex.name]=tex
            except Exception as e:scan_errors.append(dict(path=str(path),error=str(e)))
        for i,(name,tex) in enumerate(sorted(textures.items())):
            try:
                if not re.fullmatch('[A-Za-z0-9_.-]+',name):raise ValueError('unsafe texture name')
                old=cached.get(name)
                if old and (old['source'],old['offset'],old['packed_bytes'])==(str(tex.filePath),tex.offset,tex.size):
                    target=Path(old.get('file',a.reuse.parent/(name+'.png'))).resolve()
                    if target.exists() and Path(tex.filePath).stat().st_mtime<=target.stat().st_mtime and hashlib.sha256(target.read_bytes()).hexdigest()==old['sha256']:
                        exported.append(dict(old,file=str(target),reused_from=str(a.reuse.resolve())))
                        continue
                image,floats=decode_texture(tex)
                if floats is not None:
                    target=out/(name+'.npy');np.save(target,floats)
                    dimensions=[floats.shape[1],floats.shape[0]];mode='RGBA16F_data'
                else:
                    target=out/(name+'.png');image.save(target);dimensions=list(image.size);mode=image.mode
                exported.append(dict(name=name,source=str(tex.filePath),offset=tex.offset,packed_bytes=tex.size,
                    file=str(target),size=dimensions,mode=mode,sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
            except Exception as e:failures.append(dict(name=name,error=str(e)))
            if (i+1)%100==0:print(f'Textures {i+1}/{len(textures)}, failures {len(failures)}',file=sys.__stdout__,flush=True)
        missing=sorted(required-set(textures))
    report=dict(status='passed' if not (missing or scan_errors or failures) else 'partial_or_failed',
        level=a.level,required=len(required),indexed=len(textures),exported=exported,missing=missing,
        scan_errors=scan_errors,failures=failures,landclass_textures=sorted(land_refs),landclass_aliases=sorted(aliases))
    (out/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=report['status'],required=len(required),exported=len(exported),missing=missing,
        scan_errors=scan_errors,failures=failures),indent=2))
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':sys.exit(main())
