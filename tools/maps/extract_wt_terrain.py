"""Decode the actual HM2 heightmap without changing the original DBLD game map.

Format verified against Gaijin's heightmapPhysHandler.cpp, compressedHeightmap.cpp
and dag_compressedHeightmap.h. Whole-level landmesh is separate and not silently
substituted by this fine heightmap region.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import numpy as np
from PIL import Image
from dagor_codec import configure_oodle, framed


def chunks(data):
    if data[:8]!=b'DBLD3x64':raise ValueError('unsupported level container')
    offset=16+struct.unpack_from('<I',data,12)[0]
    while offset+8<=len(data):
        flags,tag=struct.unpack_from('<I4s',data,offset);size=flags&0x3fffffff
        if size<4 or offset+4+size>len(data):raise ValueError('chunk exceeds level')
        yield tag.strip(b'\0').decode('ascii'),offset+8,data[offset+8:offset+4+size]
        offset+=4+size


def heightfield(payload):
    cell,low,scale,ox,oy,wv,hv,*exclude=struct.unpack_from('<5f6I',payload)
    version=wv>>24;width=wv&0xffffff;height=hv&0x7fffffff
    if version!=2 or not 8<=width<=8192 or not 8<=height<=8192 or not 0<cell<=256:
        raise ValueError(f'unsupported HM2 metadata: {version=}, {width=}, {height=}, {cell=}')
    config=struct.unpack_from('<I',payload,44)[0];shift=config&255;tile=1<<shift
    if shift>5 or width%tile or height%tile:raise ValueError('unsupported heightmap blocks')
    bw,bh=width//tile,height//tile;blocks=bw*bh;pixels=tile*tile
    hrb_size=1<<((config>>8)&15) if config&0xf00 else 0
    levels=(width//hrb_size).bit_length()-1 if hrb_size else 0
    if hrb_size and width != hrb_size*(1<<levels):raise ValueError('invalid hierarchy size')
    hierarchy=((4**levels-1)//3)*16
    chunk_size=config&~0xfff;per_chunk=chunk_size//pixels if chunk_size else 0
    count=(blocks+per_chunk-1)//per_chunk if per_chunk else 0
    pos=48;parts=[];headers=None
    for index in range(count+1):
        variance_count=blocks*pixels if not count else min(per_chunk,blocks-(index-1)*per_chunk)*pixels if index else 0
        expected=variance_count+(blocks*4+hierarchy if index==0 else 0)
        raw,pos=framed(payload,pos,expected)
        if index==0:
            headers=np.frombuffer(raw[:blocks*4],dtype='<u2').reshape(blocks,2).astype(np.uint32)
            if not count:parts.append(raw[blocks*4:blocks*4+variance_count])
        else:parts.append(raw)
    encoded=np.frombuffer(b''.join(parts),dtype=np.uint8).reshape(blocks,pixels)
    variance=np.cumsum(encoded,axis=1,dtype=np.uint32)&255
    values=headers[:,0:1]+(variance*headers[:,1:2]+127)//255
    if np.max(values)>65535:raise ValueError('decoded height exceeds uint16')
    grid=values.reshape(bh,bw,tile,tile).transpose(0,2,1,3).reshape(height,width).astype('<u2')
    metres=(low+grid.astype(np.float64)*scale/65535).astype('<f4')
    metadata=dict(width=width,height=height,cell_m=cell,origin_dagor_xz_m=[ox,oy],hmin_m=low,hscale_m=scale,
        version=version,block_shift=shift,compressed_chunks=count+1,consumed=pos,payload_bytes=len(payload),
        minimum_m=float(metres.min()),maximum_m=float(metres.max()),exclude_bounding=exclude,
        fine_region_m=[width*cell,height*cell],scope='actual HM2 fine region; not the outer whole-level landmesh')
    return grid,metres,metadata


def main():
    p=argparse.ArgumentParser();p.add_argument('--map',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--oodle-library',required=True)
    a=p.parse_args();source=a.map.resolve(strict=True);out=a.output.resolve()
    if out==source.parent or source.is_relative_to(out):raise ValueError('output must be separate from source')
    configure_oodle(a.oodle_library);data=source.read_bytes()
    hm=[(o,b) for name,o,b in chunks(data) if name=='HM2']
    if len(hm)!=1:raise ValueError('expected one HM2 region')
    grid,metres,report=heightfield(hm[0][1]);out.mkdir(parents=True,exist_ok=False)
    grid.tofile(out/'height.u16');metres.tofile(out/'height.f32')
    Image.fromarray(grid).save(out/'height.png')
    report.update(source=str(source),sha256=hashlib.sha256(data).hexdigest(),payload_offset=hm[0][0])
    (out/'terrain.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
