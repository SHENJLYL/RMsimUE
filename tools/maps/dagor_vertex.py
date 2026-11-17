"""Decode explicit CompiledShaderChannelId records, preserving all texture coordinates.

Enums/layout: official dag_shaderCommon.h and drv/3d/dag_consts_base.h.
The returned UVs use bottom-left image convention for Blender/FBX export.
"""
import numpy as np

TYPES={0:('<f4',1),1:('<f4',2),2:('<f4',3),3:('<f4',4),4:('u1',4),5:('u1',4),
       6:('<i2',2),7:('<i2',4),9:('<i2',2),10:('<i2',4),11:('<u2',2),12:('<u2',4),
       13:('<u4',1),14:('<u4',1),15:('<f2',2),16:('<f2',4),
       17:('<i4',1),18:('<i4',2),19:('<i4',3),20:('<i4',4),
       21:('<u4',1),22:('<u4',2),23:('<u4',3),24:('<u4',4)}


def decode_vertices(raw,declarations,stride):
    if len(raw)%stride:raise ValueError('incomplete vertex stride')
    count=len(raw)//stride;offset=0;position=None;uvs={};normals=None
    for type_value,usage,index,stream in declarations:
        if stream!=0:raise ValueError('multi-stream source vertices need an explicit adapter')
        kind=type_value>>16
        if kind not in TYPES:raise ValueError(f'unknown vertex type {type_value:#x}')
        dtype,n=TYPES[kind];item=np.dtype(dtype).itemsize
        if offset+n*item>stride:raise ValueError('vertex declaration exceeds stride')
        values=np.ndarray((count,n),dtype=dtype,buffer=raw,offset=offset,strides=(stride,item)).astype(np.float32)
        offset+=n*item
        if usage==0 and index==0:
            if n<3:raise ValueError('position has fewer than three components')
            position=values[:,:3]
            if kind in (7,10):position=np.maximum(position/32767.,-1)
            elif kind==12:position/=65535.
            elif kind not in (2,3,16):raise ValueError(f'unhandled position encoding {kind}')
        if usage in (3,4):
            if n<2:raise ValueError('texture coordinate has fewer than two components')
            values=values[:,:2]
            if kind in (6,7):values/=4096.
            elif kind in (9,10):values=np.maximum(values/32767.,-1)
            elif kind in (11,12):values/=65535.
            elif kind not in (1,2,3,15,16):raise ValueError(f'unhandled UV encoding {kind}')
            values[:,1]=1-values[:,1]
            key=index if usage==3 else 16+index
            if key in uvs:raise ValueError('duplicate UV semantic')
            uvs[key]=values.tolist()
        if usage==1 and index==0:
            if kind==4:normals=values[:,[2,1,0]]/127.5-1
            elif kind in (2,3):normals=values[:,:3]
            elif kind==10:normals=np.maximum(values[:,:3]/32767.,-1)
    if offset!=stride:raise ValueError(f'vertex declaration consumes {offset}, stride is {stride}')
    if position is None or not np.all(np.isfinite(position)):raise ValueError('invalid/missing positions')
    if normals is None:normals=np.zeros_like(position)
    lengths=np.linalg.norm(normals,axis=1,keepdims=True)
    normals=np.divide(normals,lengths,out=np.zeros_like(normals),where=lengths>1e-8)
    return position.tolist(),uvs.get(0,[[0.,0.] for _ in range(count)]),uvs,normals.tolist()
