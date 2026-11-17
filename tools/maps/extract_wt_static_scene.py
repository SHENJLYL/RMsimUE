"""Decode scn2 static prefabs from the original level using the official SceneHdr layout."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import zlib
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
    source=a.map.read_bytes();blocks={name:b for name,_,b in chunks(source)}
    t=BinFile(blocks['DxP2']);t.seek(8);tex_count=readInt(t);texture_map=[]
    for i in range(tex_count):
        size=readInt(t);texture_map.append(t.read(size).rstrip(b'\0').decode('utf-8'));t.seek((-size)%4,1)
    f=BinFile(blocks['SCN']);hdr=struct.unpack('<8I2f4I',f.read(56))
    magic,version,tc,mc,vc,mh,lc,lf,ls,ds,object_count,fade_count,light_count,object_bytes=hdr
    if magic!=int.from_bytes(b'scn2','little') or version<0x20130524 or lc or light_count:
        raise ValueError('unsupported static scene revision/light catalog')
    rest=readInt(f);ids=struct.unpack('<'+str(tc)+'I',f.read(tc*4));names=[texture_map[i] for i in ids]
    mvd=MatVData(CompressedData(f).decompressToBin(),a.map.stem+'_static',tc,mc,textures=names)
    mvd.headerSize=mh&0x3fffffff;mvd.computeData()
    raw=zlib.decompress(f.read(f.getSize()-f.tell()))
    header=raw[:object_bytes];table,count=struct.unpack_from('<2I',header)
    if count!=object_count:raise ValueError('static object count mismatch')
    meshes=BinFile(raw[object_bytes:]);cache={};entries=[]
    materials=[dict(name=m.getName(),shader=m.cls,parameters=m.par,texture_slots=list(m.getTextureSlots())) for m in mvd.getMaterials()]
    for i in range(count):
        ptr=table+i*112;lod_ptr,lod_count=struct.unpack_from('<2I',header,ptr+16)
        # BSphere3 stores radius AND radius squared (20 bytes), followed by visrange.
        bounds=np.array(struct.unpack_from('<6f',header,ptr+56)).reshape(2,3)
        name_ptr=struct.unpack_from('<q',header,ptr+104)[0]
        name=header[name_ptr:header.index(0,name_ptr)].decode('utf-8') if 0<=name_ptr<len(header) else f'object_{i}'
        for lod in range(lod_count):
            mesh_size,distance=struct.unpack_from('<Qf',header,lod_ptr+lod*16)
            mesh=ShaderMesh(meshes.readBlock(mesh_size)) if mesh_size else None
            if lod or mesh is None:continue
            vertices=[];uvs=[];faces=[];mat_ids=[]
            for e in mesh.elems:
                if not e.numFace:continue
                if e.vData not in cache:cache[e.vData]=mvd.getVertexData(e.vData,decode=False)
                data=cache[e.vData];g=data.globalVertexData;stride=g.getVertexStride()
                fs=data.getFaces()[e.startI//3:e.startI//3+e.numFace]
                indices=sorted({n+e.baseVertex for face in fs for n in face})
                if indices[0]<0 or indices[-1]>=g.getVertexCnt():raise ValueError('static vertex overrun')
                buf=b''.join(data.rawVertices[n*stride:(n+1)*stride] for n in indices)
                v,uv,_,_=decode_vertices(buf,g.declarations,stride)
                start=len(vertices);lookup={old:start+j for j,old in enumerate(indices)}
                vertices.extend(v);uvs.extend(uv);faces.extend(tuple(lookup[n+e.baseVertex] for n in face) for face in fs)
                mat_ids.extend([e.mat]*len(fs))
            if not faces:continue
            v=np.array(vertices,dtype='<f4');filename=f'static_{i:04d}.npz'
            error=max(float((bounds[0]-v.min(axis=0)).max()),float((v.max(axis=0)-bounds[1]).max()))
            if error>.1:raise ValueError(f'static geometry exceeds declared bbox by {error}m')
            np.savez_compressed(out/filename,vertices=v,uvs=np.array(uvs,dtype='<f4'),
                triangles=np.array(faces,dtype='<i4'),material_ids=np.array(mat_ids,dtype='<i4'))
            entries.append(dict(name=name,index=i,file=filename,triangles=len(faces),vertices=len(vertices),
                bounds_dagor_m=dict(lo=v.min(axis=0).tolist(),hi=v.max(axis=0).tolist())))
    if meshes.getSize()-meshes.tell()!=fade_count*4:raise ValueError('unconsumed static scene data')
    report=dict(status='decoded_pending_render_validation',source=str(a.map.resolve()),sha256=hashlib.sha256(source).hexdigest(),
        objects=entries,materials=materials,textures=names,source_object_count=count,triangles=sum(e['triangles'] for e in entries))
    (out/'scene.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=report['status'],objects=len(entries),triangles=report['triangles']),indent=2))


if __name__=='__main__':main()
