"""Bind surveyed support heights from the RMUC navigation exporter to its packaged map."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import yaml


def export(columns, package, output):
    spec=json.loads((package/'SOURCE.json').read_text(encoding='utf8'))
    source=json.loads((columns.parent/'metadata.json').read_text(encoding='utf-8-sig'))
    if source['collision_sha256']!=spec['map_metadata']['collision_sha256']:
        raise ValueError('Height samples belong to different collision geometry')
    meta=yaml.safe_load((package/'map.yaml').read_text(encoding='utf8'))
    raster=(package/meta['image']).read_bytes()
    if hashlib.sha256(raster).hexdigest()!=spec['raster_sha256']:raise ValueError('Raster checksum mismatch')
    magic,dims,maximum,data=raster.split(b'\n',3)
    w,h=map(int,dims.split())
    if magic!=b'P5' or maximum.strip()!=b'255' or meta['negate'] or meta['origin'][2]!=0:
        raise ValueError('Expected upright PGM with occupied cells dark')
    pixels=np.frombuffer(data,dtype='u1').reshape(h,w)[::-1]
    with np.load(columns) as grid:
        xs,ys=grid['x'],grid['y']
        zz=np.array(grid['z'],dtype='<f4')
        if 'free' in grid:zz[~grid['free'].astype(bool)]=np.nan
    step=meta['resolution']
    if zz.shape!=(len(ys),len(xs)) or not np.allclose(np.diff(xs),step) or not np.allclose(np.diff(ys),step):
        raise ValueError('Incompatible support grid')
    cx=np.floor((xs-meta['origin'][0])/step).astype(int)
    cy=np.floor((ys-meta['origin'][1])/step).astype(int)
    inside=((cx>=0)&(cx<w))[None,:]&((cy>=0)&(cy<h))[:,None]
    free=(1-pixels.astype(float)/255)<meta['free_thresh']
    zz[~(inside&free[np.clip(cy,0,h-1)[:,None],np.clip(cx,0,w-1)[None,:]])]=np.nan
    valid=((cx>=2)&(cx<w-2))[None,:]&((cy>=2)&(cy<h-2))[:,None]
    for dx in range(-2,3):
        for dy in range(-2,3):
            sample=pixels[np.clip(cy+dy,0,h-1)[:,None],np.clip(cx+dx,0,w-1)[None,:]]
            valid &= 1-sample.astype(float)/255 < meta['free_thresh']
    header=f'RMSIM_SURVEY_V2 {len(xs)} {len(ys)} {xs[0]:.10f} {ys[0]:.10f} {step:.10f}\n'
    output.write_bytes(header.encode('ascii')+zz.tobytes()+valid.astype('u1').tobytes())
    return hashlib.sha256(output.read_bytes()).hexdigest()


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    for name in ('columns','package','output'):cli.add_argument(name,type=Path)
    args=cli.parse_args()
    print(export(args.columns,args.package,args.output))
