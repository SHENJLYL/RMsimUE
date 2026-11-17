"""Decode the original whole-level landmesh, combined geometry and ground decals.

Container and cell transforms follow Gaijin lmeshManager.cpp/lmeshRenderer.cpp.
Fine HM2 terrain remains separate; its exclusion region is kept in metadata.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import numpy as np
from dagor_codec import configure_oodle
from dagor_vertex import decode_vertices
from extract_wt_terrain import chunks


def main():
    p=argparse.ArgumentParser();p.add_argument('--map',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--oodle-library',required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    configure_oodle(a.oodle_library)
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'third_party/dagor_asset_explorer/src/dae'))
    from util.fileread import BinFile,readInt
    from util.decompression import CompressedData
    from parse.mesh import MatVData,ShaderMesh
    import util.log as log
    log.log=lambda *args,**kwargs:None
    def block(file):
        size=readInt(file)
        if size>>30:raise ValueError('unexpected compressed container block')
        return file.readBlock(size)
    source=a.map.read_bytes();payload=next(b for name,_,b in chunks(source) if name=='lmap');f=BinFile(payload)
    magic,version,grid,cell,w,h,ox,oy,tile=struct.unpack('<4sIff4iI',f.read(36))
    if magic!=b'lndm' or version!=4:raise ValueError('unsupported landmesh')
    offsets=struct.unpack('<4I',f.read(16));f.seek(36+offsets[0])
    tex_count,mat_count,vd_count,hdr_size=struct.unpack('<4I',f.read(16))
    strings=f.read(readInt(f));ptr,cnt=struct.unpack_from('<2I',strings)
    if cnt!=tex_count:raise ValueError('texture table count mismatch')
    names=[]
    for i in range(cnt):
        pos=struct.unpack_from('<I',strings,ptr+4*i)[0]
        end=strings.index(0,pos);names.append(strings[pos:end].decode('utf-8'))
    data=CompressedData(f).decompressToBin()
    mvd=MatVData(data,a.map.stem,tex_count,mat_count,textures=names);mvd.headerSize=hdr_size&0x3fffffff;mvd.computeData()
    outer=block(f);cells=[]
    for i in range(w*h):
        c=block(outer);land=block(c);lods=[]
        while land.tell()<land.getSize():
            b=block(land);lods.append(ShaderMesh(b) if b.getSize() else None)
        categories={'land':lods[0] if lods else None}
        for key in ('decal','combined','patches'):
            if c.tell()>=c.getSize():categories[key]=None;continue
            b=block(c);categories[key]=ShaderMesh(b) if b.getSize() else None
        if c.tell()!=c.getSize():raise ValueError('unconsumed cell bytes')
        cells.append(categories)
    boxes=block(outer);bounds=np.frombuffer(boxes.read(w*h*24),dtype='<f4').reshape(w*h,2,3).copy()
    radii=np.frombuffer(boxes.read(w*h*4),dtype='<f4').copy()
    if outer.tell()!=outer.getSize() or f.tell()!=36+offsets[1]:raise ValueError('mesh/detail boundary mismatch')
    valid=np.all(bounds[:,0]<=bounds[:,1],axis=1)
    lo=bounds[valid,0].min(axis=0);hi=bounds[valid,1].max(axis=0)
    scale=np.array([cell/2,(hi[1]-lo[1])/2,cell/2])
    materials=[dict(name=m.getName(),shader=m.cls,parameters=m.par,texture_slots=list(m.getTextureSlots())) for m in mvd.getMaterials()]
    result=dict(source=str(a.map.resolve()),sha256=hashlib.sha256(source).hexdigest(),map_size=[w,h],
        origin_cells=[ox,oy],cell_m=cell,grid_m=grid,bounds_dagor_m=dict(lo=lo.tolist(),hi=hi.tolist()),
        materials=materials,textures=names,cells=[])
    cache={};triangles_total=0
    for i,category in enumerate(cells):
        offset=np.array([cell*(i%w+ox+.5),(hi[1]+lo[1])/2,cell*(i//w+oy+.5)])
        for kind,mesh in category.items():
            if mesh is None or not mesh.elems:continue
            vertices=[];triangles=[];uvs=[];mat_ids=[]
            for e in mesh.elems:
                if not e.numFace:continue
                if e.vData not in cache:cache[e.vData]=mvd.getVertexData(e.vData,decode=False)
                vd=cache[e.vData];g=vd.globalVertexData;stride=g.getVertexStride()
                fs=vd.getFaces()[e.startI//3:e.startI//3+e.numFace]
                if len(fs)!=e.numFace:raise ValueError('landmesh index overrun')
                indices=sorted({n+e.baseVertex for face in fs for n in face})
                if indices[0]<0 or indices[-1]>=g.getVertexCnt():raise ValueError('landmesh vertex overrun')
                raw=b''.join(vd.rawVertices[n*stride:(n+1)*stride] for n in indices)
                v,uv,_,_=decode_vertices(raw,g.declarations,stride);v=np.asarray(v)*scale+offset
                if not np.all(np.isfinite(v)):raise ValueError('nonfinite world landmesh')
                start=len(vertices);lookup={old:start+j for j,old in enumerate(indices)}
                vertices.extend(v.tolist());uvs.extend(uv)
                triangles.extend(tuple(lookup[n+e.baseVertex] for n in face) for face in fs);mat_ids.extend([e.mat]*len(fs))
            if not triangles:continue
            filename=f'cell_{i:04d}_{kind}.npz';v=np.array(vertices,dtype='<f4')
            np.savez_compressed(out/filename,vertices=v,uvs=np.array(uvs,dtype='<f4'),
                triangles=np.array(triangles,dtype='<i4'),material_ids=np.array(mat_ids,dtype='<i4'))
            result['cells'].append(dict(cell=i,kind=kind,file=filename,triangles=len(triangles),vertices=len(vertices),
                bounds_dagor_m=dict(lo=v.min(axis=0).tolist(),hi=v.max(axis=0).tolist())))
            triangles_total+=len(triangles)
    result.update(status='decoded_pending_world_validation',triangles=triangles_total)
    (out/'landmesh.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=result['status'],map_size=[w,h],mesh_parts=len(result['cells']),triangles=triangles_total,
        bounds=result['bounds_dagor_m']),indent=2))


if __name__=='__main__':main()
