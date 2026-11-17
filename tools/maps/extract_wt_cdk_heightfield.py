"""Python 3.14+: read an official CDK sample heightfield, crop without changing source.

This does not decode or claim to reproduce a shipped War Thunder binary level.
"""
import argparse
import array
from compression import zstd
import hashlib
import json
from pathlib import Path
import re
import sys


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--output',required=True)
    p.add_argument('--center-x',type=int,default=1120);p.add_argument('--center-y',type=int,default=1120);p.add_argument('--half-cells',type=int,default=16)
    a=p.parse_args();source=Path(a.source).resolve();out=Path(a.output).resolve();out.mkdir(parents=True,exist_ok=True)
    settings=(source/'heightmapLand.plugin.blk').read_text(encoding='utf-8')
    def setting(key,kind):
        m=re.search(r'^'+re.escape(key)+':'+kind+r'=([^\r\n]+)',settings,re.M)
        if not m:raise ValueError('missing '+key)
        return float(m.group(1))
    width=int(setting('heightMapSizeX','i'));height=int(setting('heightMapSizeY','i'))
    scale=setting('heightScale','r');offset=setting('heightOffset','r');cell=setting('gridCellSize','r')
    packed=(source/'heightmap.r32.zst').read_bytes();raw=zstd.decompress(packed)
    if len(raw)!=width*height*4:raise ValueError('heightfield dimensions do not match raw float count')
    values=array.array('f');values.frombytes(raw)
    if sys.byteorder!='little':values.byteswap()
    x,y,n=a.center_x,a.center_y,a.half_cells
    if n<1 or x-n<0 or x+n>=width or y-n<0 or y+n>=height:raise ValueError('crop outside heightfield')
    reference=values[y*width+x]*scale+offset
    patch=array.array('f',(values[row*width+col]*scale+offset-reference
        for row in range(y-n,y+n+1) for col in range(x-n,x+n+1)))
    if sys.byteorder!='little':patch.byteswap()
    (out/'height.f32').write_bytes(patch.tobytes())
    report={'schema':'wt_cdk_height_crop_v1','source_file':str(source/'heightmap.r32.zst'),
        'source_sha256':hashlib.sha256(packed).hexdigest(),'settings_sha256':hashlib.sha256((source/'heightmapLand.plugin.blk').read_bytes()).hexdigest(),
        'source_dimensions':[width,height],'crop_center_pixel':[x,y],'patch_samples':2*n+1,'cell_size_m':cell,
        'size_m':[2*n*cell,2*n*cell],'height_scale':scale,'height_offset':offset,'removed_reference_height_m':reference,
        'local_height_min_m':min(patch),'local_height_max_m':max(patch),
        'frame':'local X follows source columns, local Y follows rows, Z is height; not a geographic or game referee frame',
        'limitations':['CDK location_sample crop, not a shipped small battle map','terrain only; no buildings/foliage/material layers','16m original sampling; no invented high-frequency detail']}
    (out/'height-source.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
