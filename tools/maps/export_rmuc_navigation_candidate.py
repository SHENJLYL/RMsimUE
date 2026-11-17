"""Geometry-derived single-layer navigation candidate; physical route acceptance is separate."""
import argparse,collections,hashlib,json,math,sys,time
from pathlib import Path
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
sys.path.insert(0,str(Path(__file__).resolve().parent))
from prepare_rmuc2027 import read_glb,world_primitives

def main():
    p=argparse.ArgumentParser();p.add_argument('--columns',required=True);p.add_argument('--source',required=True);p.add_argument('--descriptor',required=True);p.add_argument('--map',required=True);p.add_argument('--output',required=True)
    a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
    column_dir=Path(a.columns);report=json.loads((column_dir/'geometry-report.json').read_text(encoding='utf-8'));source=Path(a.source);descriptor=Path(a.descriptor)
    assert hashlib.sha256(source.read_bytes()).hexdigest()==report['source_collision_sha256']
    assert hashlib.sha256(descriptor.read_bytes()).hexdigest()==report['descriptor_sha256']
    data=np.load(column_dir/'vertical-surfaces.npz');z=data['z'];nz=data['normal_z'];xs=data['x'];ys=data['y'];r=float(xs[1]-xs[0]);rows,cols,layers=z.shape
    assert not data['truncated'].any()
    descriptor_data=json.loads(descriptor.read_text(encoding='utf-8'));doc,blob=read_glb(source);translation=np.array(descriptor_data['source_to_sim_translation_m'])
    vertices=[];faces=[];offset=0
    for name,v,n,f,material in world_primitives(doc,blob):vertices.extend((v+translation).tolist());faces.extend((f+offset).tolist());offset+=len(v)
    tree=BVHTree.FromPolygons(vertices,faces,all_triangles=True);del vertices,faces
    started=time.monotonic();above=np.concatenate([np.full((rows,cols,1),np.inf),z[:,:,:-1]],axis=2)
    # The roof-to-ground vertical clearance grows when the chassis support plane tilts.
    clearance=.4192/np.maximum(nz,.01)+.03
    valid=np.isfinite(z)&(nz>=math.cos(math.radians(50)))&((above-z)>clearance)
    slope=np.sqrt(np.maximum(0,1-nz*nz))/np.maximum(nz,.001)
    candidates={(i,j):np.flatnonzero(valid[i,j]).tolist() for i in range(rows) for j in range(cols)}
    reached=np.zeros(z.shape,dtype=bool);queue=collections.deque();edge_cache={};blocked_edges=set();traces=0
    scene=json.loads(Path(a.map).read_text(encoding='utf-8'))
    for spawn in scene['spawns']:
        x,y,_=spawn['position_m'];i=int(np.argmin(abs(ys-y)));j=int(np.argmin(abs(xs-x)));ks=candidates[i,j]
        if not ks:raise ValueError('Spawn lacks support/headroom: '+spawn['id'])
        k=min(ks,key=lambda t:z[i,j,t]);reached[i,j,k]=True;queue.append((i,j,k))
    def connected(first,second):
        nonlocal traces
        key=tuple(sorted((first,second)))
        if key in edge_cache:return edge_cache[key]
        i,j,k=first;ii,jj,kk=second;h=float(z[i,j,k]);other=float(z[ii,jj,kk])
        # Flat abrupt curbs above35mm are blocked; sampled continuous slopes follow their normals.
        link_slope=max(float(slope[i,j,k]),float(slope[ii,jj,kk]))
        # A sinusoidal surface can be steeper between the endpoint samples. Measure it;
        # do not relax the abrupt-step threshold to make corrugations appear traversable.
        if abs(other-h)>.03:
            midpoint=Vector((float((xs[j]+xs[jj])/2),float((ys[i]+ys[ii])/2),max(h,other)+.15))
            point,normal,index,hit=tree.ray_cast(midpoint,Vector((0,0,-1)),abs(other-h)+.3);traces+=1
            if point is not None and normal.z>=math.cos(math.radians(50)):
                link_slope=max(link_slope,math.sqrt(max(0,1-normal.z*normal.z))/normal.z)
        limit=max(.035,r*link_slope+.005)
        ok=abs(other-h)<=limit
        if ok:
            for offset in (.0425,.21,.4192):
                start=Vector((float(xs[j]),float(ys[i]),h+offset));end=Vector((float(xs[jj]),float(ys[ii]),other+offset));delta=end-start;distance=delta.length
                point,normal,index,hit=tree.ray_cast(start,delta.normalized(),distance);traces+=1
                if point is not None and hit<distance-1e-5:ok=False;break
        edge_cache[key]=ok
        if not ok:blocked_edges.add(key)
        return ok
    while queue:
        current=queue.popleft();i,j,k=current
        for di,dj in ((-1,0),(1,0),(0,-1),(0,1)):
            ii,jj=i+di,j+dj
            if not(0<=ii<rows and 0<=jj<cols):continue
            for kk in candidates[ii,jj]:
                other=(ii,jj,kk)
                if connected(current,other) and not reached[other]:reached[other]=True;queue.append(other)
    count=reached.sum(axis=2)
    if (count>1).any():raise ValueError('Multiple reachable levels need separate maps/planning; refusing lossy projection')
    free=count==1;ground=np.full((rows,cols),np.nan,dtype='<f4')
    idx=np.argmax(reached,axis=2);ground[free]=np.take_along_axis(z,idx[:,:,None],axis=2)[:,:,0][free]
    # A raster cannot encode blocked edges. Represent each such boundary on one side;
    # keep the higher side for ledges, deterministic positive-grid side for thin walls.
    barrier=np.zeros((rows,cols),dtype=bool)
    for first,second in blocked_edges:
        if not reached[first] or not reached[second]:continue
        i,j,k=first;ii,jj,kk=second
        high=(i,j) if z[first]>z[second]+.005 else (ii,jj) if z[second]>z[first]+.005 else max((i,j),(ii,jj))
        barrier[high]=True
    free &= ~barrier
    pixels=np.where(free,254,0).astype('u1');origin=[float(xs[0]-r/2),float(ys[0]-r/2),0.0]
    (out/'map.pgm').write_bytes(f'P5\n{cols} {rows}\n255\n'.encode()+np.flipud(pixels).tobytes())
    (out/'map.yaml').write_text(f'image: map.pgm\nresolution: {r}\norigin: {origin}\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n',encoding='utf-8')
    np.savez_compressed(out/'support-heights.npz',x=xs,y=ys,z=ground,free=free,barriers=barrier)
    eligible=free.copy();radius=math.ceil(.36/r)
    for di in range(-radius,radius+1):
        for dj in range(-radius,radius+1):
            if math.hypot(di*r,dj*r)>.36:continue
            shifted=np.zeros_like(free)
            ai=slice(max(0,di),min(rows,rows+di));aj=slice(max(0,dj),min(cols,cols+dj));bi=slice(max(0,-di),min(rows,rows-di));bj=slice(max(0,-dj),min(cols,cols-dj))
            shifted[ai,aj]=free[bi,bj];eligible &= shifted
    points={}
    for name,x,y in [('spawn',-8,-2),('forward_goal',-6,-2),('red_wave',7,6.4),('blue_wave',-7,-6.4),('centre',0,0)]:
        i=int(np.argmin(abs(ys-y)));j=int(np.argmin(abs(xs-x)))
        points[name]=dict(x=float(xs[j]),y=float(ys[i]),free=bool(free[i,j]),envelope_free=bool(eligible[i,j]),support_z=float(ground[i,j]) if np.isfinite(ground[i,j]) else None)
    raw=Path(a.map).read_bytes()
    metadata=dict(map_id=scene['id'],source_sha256=hashlib.sha256(raw).hexdigest(),source_md5=hashlib.md5(raw).hexdigest(),resolution=r,origin=origin,width=cols,height=rows,
        semantics='geometry-derived reachable support/headroom and collision-checked adjacency; no robot inflation in PGM',status='candidate_not_native_route_validated',
        collision_sha256=report['source_collision_sha256'],columns_report_sha256=hashlib.sha256((column_dir/'geometry-report.json').read_bytes()).hexdigest(),
        candidate_surfaces=int(valid.sum()),reachable_surfaces=int(reached.sum()),free_cells=int(free.sum()),barrier_cells=int(barrier.sum()),horizontal_queries=traces,wall_seconds=time.monotonic()-started,
        parameters={'body_height_m':.4192,'headroom_margin_m':.03,'max_candidate_slope_deg':50,'flat_step_limit_m':.035,'probe_heights_m':[.0425,.21,.4192]},samples=points,
        limits=['Not proof of all physically traversable routes','Needs actual AMCL and Nav2 validation on the original field','Thin features and footprint support remain resolution dependent','50deg candidates do not assert calibrated climb performance'])
    (out/'metadata.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8');print(json.dumps(metadata,indent=2),flush=True)

if __name__=='__main__':main()
