"""Read original Dagor v3 colliders, including compact dBLi quad-BVH geometry.

Source format authority: DagorEngine 9639942de5a8e5b01d91977f60ed82cda22d5f41,
collResStream.h, collisionGameResLoad.cpp, bvhIO.cpp and dag_swBLAS_leaf.h.
Game inputs remain read-only. NPZ coordinates remain original Dagor model axes.
"""
import argparse
from collections import Counter
from compression import zstd
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import numpy as np
from dagor_codec import configure_oodle, oodle_decompress


class Reader:
    def __init__(self, data): self.data=data;self.pos=0
    def take(self,n):
        if n<0 or self.pos+n>len(self.data): raise ValueError(f'truncated collider at {self.pos}: need {n}, size {len(self.data)}')
        start=self.pos;self.pos+=n;return self.data[start:self.pos]
    def unpack(self,fmt): return struct.unpack('<'+fmt,self.take(struct.calcsize('<'+fmt)))


def signed13(value):
    value &=8191
    return value-8192 if value&4096 else value


def leaf_triangles(w0,w1,w2,w3):
    base=w1&0xffffff
    a,b,c=signed13(w0),signed13(w0>>13),signed13(((w0>>26)&31)|((w1>>24)<<5))
    faces=[(base,base+a,base+b)]
    if c!=b:faces.append((base+(b if w2&(1<<29) else a),base+(a if w2&(1<<29) else b),base+c))
    d,e,f=signed13(w2>>16),signed13(w3),signed13(w3>>13)
    if d!=e:
        base+=w2&65535;faces.append((base,base+d,base+e))
        if f!=e:faces.append((base+(e if w2&(1<<30) else d),base+(d if w2&(1<<30) else e),base+f))
    return faces


def read_blas(r,expected_indices):
    scale=np.array(r.unpack('3f'));inverse=np.array(r.unpack('3f'));minimum=np.array(r.unpack('3f'))
    if not np.all(np.isfinite([scale,inverse,minimum])) or np.min(scale)<=0 or np.max(np.abs(scale*inverse-1))>1e-3:
        raise ValueError('invalid collider quantization frame')
    magic,version,stride,leaf_size,tree_bytes,vert_count,*box=r.unpack('4sHBBII6f')
    if magic!=b'dBLi' or version not in (1,2) or stride!=8 or leaf_size!=28 or not 0<tree_bytes<64*1024*1024 or not 0<vert_count<8*1024*1024:
        raise ValueError('unsupported dBLi header')
    flags=r.unpack('I')[0] if version==2 else 0
    if flags&~1:raise ValueError('unknown dBLi flags')
    packed=np.frombuffer(r.take(vert_count*8),dtype='<u8')
    points=np.stack([(packed>>shift)&0x1fffff for shift in (0,21,42)],axis=1).astype(np.float64)/32*inverse+minimum
    faces=[];material_indices=[];tree_consumed=0;leaves=0
    def subtree(depth=0):
        nonlocal tree_consumed,leaves
        if depth>256:raise ValueError('dBLi depth exceeded')
        code=r.unpack('B')[0]
        if code in (0,253,254,255):
            tree_consumed+=28;leaves+=1
            if code in (253,254):w0,w1=r.unpack('2I');w2=(1<<29) if code==253 else 0;w3=0
            else:w0,w1,w2,w3=r.unpack('4I')
            if not w0&0x80000000:raise ValueError('dBLi leaf flag missing')
            if code==255:
                if w0!=0x80000000 or w1 or w2 or w3:raise ValueError('invalid no-hit leaf')
                return
            triangles=leaf_triangles(w0,w1,w2,w3)
            if min(min(f) for f in triangles)<0 or max(max(f) for f in triangles)>=vert_count:raise ValueError('dBLi index outside vertex stream')
            faces.extend(triangles);material_indices.extend([(w3>>26)&63]*len(triangles))
        else:
            tree_consumed+=16
            if code>252:raise ValueError('invalid child count')
            for _ in range(code):subtree(depth+1)
        if tree_consumed>tree_bytes:raise ValueError('dBLi tree overruns declared runtime bytes')
    while tree_consumed<tree_bytes:subtree()
    if flags&1:r.take(leaves*2)
    if len(faces)*3!=expected_indices:raise ValueError(f'dBLi triangle count {len(faces)*3} differs from node {expected_indices}')
    return points,np.array(faces,dtype='<i4').reshape(-1,3),np.array(material_indices,dtype='<u2')


def box_mesh(bounds):
    lo,hi=np.asarray(bounds).reshape(2,3)
    vertices=np.array([[hi[a] if bit&(1<<a) else lo[a] for a in range(3)] for bit in range(8)])
    faces=np.array([(0,2,3),(0,3,1),(4,5,7),(4,7,6),(0,1,5),(0,5,4),
                    (2,6,7),(2,7,3),(0,4,6),(0,6,2),(1,3,7),(1,7,5)],dtype='<i4')
    return vertices,faces


def capsule_mesh(a,b,radius,tolerance):
    a,b=np.asarray(a),np.asarray(b);axis=b-a;length=np.linalg.norm(axis)
    direction=axis/length if length>1e-12 else np.array([0.,1.,0.])
    # Chord sagitta bound <= tolerance on both latitude and longitude arcs.
    step=2*math.acos(max(-1.,min(1.,1-tolerance*.5/max(radius,1e-12))))
    slices=max(12,math.ceil(2*math.pi/max(step,1e-3)));quarters=max(3,math.ceil(math.pi/2/max(step,1e-3)))
    helper=np.eye(3)[np.argmin(np.abs(direction))];right=np.cross(direction,helper);right/=np.linalg.norm(right);forward=np.cross(direction,right)
    rings=[]
    for angle in np.linspace(-math.pi/2,0,quarters+1):rings.append((a+direction*(radius*math.sin(angle)),radius*math.cos(angle)))
    for angle in np.linspace(0,math.pi/2,quarters+1):rings.append((b+direction*(radius*math.sin(angle)),radius*math.cos(angle)))
    vertices=np.array([center+rr*(right*math.cos(2*math.pi*j/slices)+forward*math.sin(2*math.pi*j/slices)) for center,rr in rings for j in range(slices)])
    faces=[]
    for ring in range(len(rings)-1):
        for j in range(slices):
            u=ring*slices+j;v=ring*slices+(j+1)%slices
            faces.extend(((u,v,v+slices),(u,v+slices,u+slices)))
    return vertices,np.array(faces,dtype='<i4')


def parse_resource(data,tolerance=.001):
    file=Reader(data);magic,frame=file.unpack('II')
    if magic!=0xace50003:raise ValueError(f'unsupported collider magic {magic:#x}')
    body=file.take(frame&0x3fffffff);codec=frame>>30
    if codec==1:body=zstd.decompress(body)
    elif codec==2:body=oodle_decompress(body)
    elif codec!=0:raise ValueError('unsupported collision compression')
    r=Reader(body);header=r.unpack('15fI11I');bounds=np.array(header[8:14]).reshape(2,3);counts=header[16:];n=counts[0]
    if n>65535 or counts[1]!=n or counts[2] not in (0,n) or counts[3] not in (0,n) or counts[5]:raise ValueError('invalid collider array counts')
    binds=[dict(zip(('max_tm_scale','flags','status','behavior'),r.unpack('fBBH'))) for _ in range(n)]
    nodes=[]
    for i in range(n):
        values=r.unpack('IIHhHHHBB7f')
        node=dict(zip(('name_offset','indices_count','behavior','phys_mat_id','inside_node','capsule_or_planes','planes_count','flags','type'),values[:9]))
        node.update(bounds_local=np.array(values[9:15]).reshape(2,3),radius=values[15],bind=binds[i])
        nodes.append(node)
    matrices=np.frombuffer(r.take(n*48),dtype='<f4').reshape(n,4,3).transpose(0,2,1).copy()
    r.take(counts[2]*48);r.take(counts[3]*48)
    for node in nodes:
        if node['indices_count']:node['geometry']=read_blas(r,node['indices_count'])
    pool=np.frombuffer(r.take(counts[6]*2),dtype='<u2').copy()
    capsules=np.frombuffer(r.take(counts[7]*28),dtype='<f4').reshape(-1,7).copy()
    r.take(counts[8]*16);names=r.take(counts[9]);material_bytes=r.take(counts[10])
    if material_bytes and not material_bytes.endswith(b'\0'):raise ValueError('unterminated physical material names')
    # Empty material names are real entries; stripping all NULs would lose trailing defaults.
    materials=material_bytes[:-1].decode('utf-8').split('\0') if material_bytes else []
    tail=[]
    while r.pos<len(body):
        tag,size=r.unpack('II');r.take(size);tail.append(dict(tag=tag,bytes=size))
    vertices=[];triangles=[];node_ids=[];face_material=[];records=[];offset=0
    for i,node in enumerate(nodes):
        pos=node['name_offset'];name=names[pos:names.index(0,pos)].decode('utf-8')
        record={k:v for k,v in node.items() if k not in ('geometry','bounds_local')};record.update(name=name,bounds_local=node['bounds_local'].tolist(),authored_tm=matrices[i].tolist())
        status=node['bind']['status'];phys=node['bind']['behavior']&2
        active=phys and status&1 and not status&4 and node['radius']>=0
        record['included_physics']=bool(active)
        if not active:records.append(record);continue
        kind=node['type'];labels=None
        if kind in (0,5):
            if not node['indices_count']:record['included_physics']=False;record['empty_geometry']=True;records.append(record);continue
            v,f,labels=node['geometry'];record['representation']='decoded_original_quantized_triangles'
        elif kind==2:v,f=box_mesh(node['bounds_local']);record['representation']='exact_analytic_box_triangulation'
        elif kind==4:
            cap=capsules[node['capsule_or_planes']];v,f=capsule_mesh(cap[:3],cap[4:7],float(cap[3]),tolerance/max(node['bind']['max_tm_scale'],1))
            record.update(representation='tessellated_original_capsule',capsule=cap.tolist(),surface_tolerance_m=tolerance)
        elif kind==3:
            center=np.mean(node['bounds_local'],axis=0);v,f=capsule_mesh(center,center,node['radius'],tolerance/max(node['bind']['max_tm_scale'],1));record.update(representation='tessellated_original_sphere',surface_tolerance_m=tolerance)
        else:raise ValueError(f'unsupported physical primitive {kind}')
        # At bind, retained/eps-IDENT primitive geometry is already in resource space.
        uses_stored=(status&2) or (kind in (2,3) and node['flags']&1)
        if not uses_stored:
            tm=matrices[i];v=v@tm[:,:3].T+tm[:,3]
            if np.linalg.det(tm[:,:3])<0:f=f[:,[0,2,1]]
        if labels is None:labels=np.zeros(len(f),dtype='<u2')
        material=node['phys_mat_id']
        if material>=0:physical_labels=np.full(len(f),material,dtype='<i4')
        elif material==-1:physical_labels=np.full(len(f),-1,dtype='<i4')
        else:
            begin=-material-2;length=int(pool[begin]);ids=pool[begin+1:begin+1+length]
            if len(ids)!=length or labels.max(initial=0)>=length:raise ValueError('face material outside node material pool')
            physical_labels=ids[labels].astype('<i4');physical_labels[physical_labels==65535]=-1
        if physical_labels.max(initial=-1)>=len(materials):raise ValueError('unknown physical material index')
        # Degenerate poles of tessellated capsules contribute no collision and are removed.
        cross=np.cross(v[f[:,1]]-v[f[:,0]],v[f[:,2]]-v[f[:,0]])
        keep=np.linalg.norm(cross,axis=1)>1e-12
        record['degenerate_triangles_removed']=int(np.count_nonzero(~keep))
        f=f[keep];physical_labels=physical_labels[keep]
        vertices.append(v);triangles.append(f+offset);node_ids.extend([i]*len(f));face_material.extend(physical_labels.tolist());offset+=len(v)
        record.update(vertices=len(v),triangles=len(f));records.append(record)
    v=np.concatenate(vertices).astype('<f4') if vertices else np.empty((0,3),dtype='<f4')
    f=np.concatenate(triangles).astype('<i4') if triangles else np.empty((0,3),dtype='<i4')
    if len(v):
        outside=max(float((bounds[0]-v.min(axis=0)).max()),float((v.max(axis=0)-bounds[1]).max()))
        if not np.all(np.isfinite(v)) or outside>.02:raise ValueError(f'collider exceeds resource bounds by {outside}m')
    return (dict(vertices=v,triangles=f,node_ids=np.array(node_ids,dtype='<i4'),physical_material_ids=np.array(face_material,dtype='<i4')),
        dict(nodes=records,physical_materials=materials,counts=list(counts),source_bounds_dagor_m=bounds.tolist(),tail_records=tail,body_bytes=len(body),status='passed'))


def self_test():
    # A single triangle leaf has o1=1,o2=o3=2. Its disk W1 is a vertex index,
    # never the runtime byte address used by the engine's internal tree.
    w0=0x80000000|1|(2<<13)|(2<<26)
    assert leaf_triangles(w0,0,0,0)==[(0,1,2)]
    # Second A triangle is a strip; flip changes its first two vertices only.
    q0=0x80000000|1|(2<<13)|(3<<26)
    assert leaf_triangles(q0,0,0,0)==[(0,1,2),(1,2,3)]
    assert leaf_triangles(q0,0,1<<29,0)==[(0,1,2),(2,1,3)]
    # A dBLi forest containing one triangle, plus its v2 active-edge word.
    packed=[0,32,32<<21]
    frame=struct.pack('<9f',1,1,1,1,1,1,0,0,0)
    header=struct.pack('<4sHBBII6f',b'dBLi',2,8,28,28,3,0,0,0,1,1,0)+struct.pack('<I',1)
    stream=frame+header+struct.pack('<3Q',*packed)+bytes([254])+struct.pack('<2I',w0,0)+b'\0\0'
    r=Reader(stream);v,f,labels=read_blas(r,3)
    assert r.pos==len(stream) and np.array_equal(f,[[0,1,2]]) and np.allclose(v,[[0,0,0],[1,0,0],[0,1,0]])
    try:read_blas(Reader(stream[:-1]),3)
    except ValueError:pass
    else:raise AssertionError('truncated flags accepted')
    # A handcrafted analytic box uses a translated authored matrix and known root bounds.
    counts=[1,1,0,0,0,0,0,0,0,5,1]
    bound=[9,18,27,11,22,33]
    body=struct.pack('<15fI11I',10,20,30,14,10,20,30,14,*bound,math.sqrt(14),0,*counts)
    body+=struct.pack('<fBBH',1,4,17,3)
    body+=struct.pack('<IIHhHHHBB7f',1,0,3,0,65535,0,0,4,2,-1,-2,-3,1,2,3,math.sqrt(14))
    body+=struct.pack('<12f',1,0,0,0,1,0,0,0,1,10,20,30)+b'\0box\0'+b'\0'
    decoded,meta=parse_resource(struct.pack('<II',0xace50003,len(body))+body)
    assert len(decoded['triangles'])==12 and np.array_equal(decoded['vertices'].min(axis=0),bound[:3])
    assert np.array_equal(decoded['vertices'].max(axis=0),bound[3:]) and meta['physical_materials']==['']
    print('Collision reader self-check: topology, vertex quantization, truncation, authored box transform and empty material name passed')


def main():
    p=argparse.ArgumentParser();selection=p.add_mutually_exclusive_group(required=True)
    selection.add_argument('--index',type=Path);selection.add_argument('--world-map',type=Path)
    selection.add_argument('--self-test',action='store_true')
    p.add_argument('--output',type=Path)
    p.add_argument('--model',action='append');p.add_argument('--oodle-library');p.add_argument('--surface-tolerance',type=float,default=.001)
    a=p.parse_args()
    if a.self_test:self_test();return 0
    if not a.output:p.error('--output is required for extraction')
    if a.oodle_library:configure_oodle(a.oodle_library)
    if not 0<a.surface_tolerance<=.01:raise ValueError('invalid primitive surface tolerance')
    world_chunks={}
    if a.world_map:
        from extract_wt_terrain import chunks
        for name,offset,data in chunks(a.world_map.read_bytes()):
            if name in ('SCol','WCol'):world_chunks[name]=(offset,data)
        if not world_chunks:raise ValueError('no static/water collider chunks found')
        index={name:dict(group=str(a.world_map.resolve()),offset=offset,level_chunk=name) for name,(offset,_) in world_chunks.items()}
    else:index=json.loads(a.index.read_text(encoding='utf-8'))
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    report=dict(status='running',requested=len(a.model or index),models=[],failures=[],coordinate_system='original Dagor model axes, Y up',
        source_commit='9639942de5a8e5b01d91977f60ed82cda22d5f41',scope='original PHYS_COLLIDABLE bind geometry; dynamic damage and trace-only shapes are separate')
    for name in a.model or sorted(index):
        try:
            entry=index[name]
            if world_chunks:raw=world_chunks[name][1]
            else:
                with open(entry['group'],'rb') as stream:
                    stream.seek(entry['offset']);head=stream.read(8);frame=struct.unpack_from('<I',head,4)[0];raw=head+stream.read(frame&0x3fffffff)
            mesh,metadata=parse_resource(raw,a.surface_tolerance)
            folder=out/name;folder.mkdir();np.savez_compressed(folder/'collision.npz',**mesh)
            metadata.update(name=name,source=entry,source_chunk_sha256=hashlib.sha256(raw).hexdigest(),
                mesh_sha256=hashlib.sha256((folder/'collision.npz').read_bytes()).hexdigest())
            (folder/'collision.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
            report['models'].append(dict(name=name,vertices=len(mesh['vertices']),triangles=len(mesh['triangles']),nodes=len(metadata['nodes'])))
        except Exception as e:report['failures'].append(dict(name=name,error=str(e)))
        if (len(report['models'])+len(report['failures']))%100==0:print(len(report['models']),len(report['failures']),flush=True)
    report['status']='passed' if not report['failures'] else 'partial_or_failed'
    (out/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({**{k:v for k,v in report.items() if k!='models'},'exported':len(report['models']),
        'triangles':sum(v['triangles'] for v in report['models'])},indent=2));return int(bool(report['failures']))


if __name__=='__main__':sys.exit(main())
