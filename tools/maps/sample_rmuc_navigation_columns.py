"""Read-only full-scene vertical geometry samples; this is NOT a navigation occupancy map."""
import argparse,hashlib,json,math,sys,time
from pathlib import Path
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
sys.path.insert(0,str(Path(__file__).resolve().parent))
from prepare_rmuc2027 import read_glb,world_primitives

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--descriptor',required=True);p.add_argument('--output',required=True);p.add_argument('--resolution',type=float,default=.1);p.add_argument('--max-layers',type=int,default=32);args=p.parse_args(sys.argv[sys.argv.index('--')+1:])
    assert .02<=args.resolution<=.2 and 1<=args.max_layers<=128
    source=Path(args.source);descriptor=Path(args.descriptor);out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
    manifest=json.loads(descriptor.read_text(encoding='utf-8'));document,blob=read_glb(source)
    assert document['asset']['extras']['source_sha256']==manifest['source_step_sha256']
    translation=np.array(manifest['source_to_sim_translation_m']);vertices=[];faces=[];groups=[];part_names=[];offset=0
    start=time.monotonic()
    for group,v,n,f,material in world_primitives(document,blob):
        if group not in part_names:part_names.append(group)
        index=part_names.index(group);v=v+translation
        vertices.extend(v.tolist());faces.extend((f+offset).tolist());groups.extend([index]*len(f));offset+=len(v)
    bounds=np.array(vertices);lo=bounds.min(axis=0);hi=bounds.max(axis=0);del bounds
    print('Building collision BVH',len(vertices),'vertices',len(faces),'triangles',flush=True)
    tree=BVHTree.FromPolygons(vertices,faces,all_triangles=True);triangle_count=len(faces);del vertices,faces
    r=args.resolution;origin=np.floor(lo[:2]/r)*r;extent=np.ceil(hi[:2]/r)*r
    xs=np.arange(origin[0]+r/2,extent[0],r);ys=np.arange(origin[1]+r/2,extent[1],r)
    max_layers=args.max_layers;z=np.full((len(ys),len(xs),max_layers),np.nan,dtype='<f4');nz=z.copy();parts=np.full(z.shape,-1,dtype='<i2');counts=np.zeros(z.shape[:2],dtype='u1');truncated=np.zeros(z.shape[:2],dtype=bool)
    bottom=float(lo[2]-.5);top=float(hi[2]+.5);epsilon=.0001
    for row,y in enumerate(ys):
        for col,x in enumerate(xs):
            height=top
            for layer in range(max_layers+1):
                point,normal,index,distance=tree.ray_cast(Vector((float(x),float(y),height)),Vector((0,0,-1)),height-bottom)
                if point is None:break
                if layer==max_layers:truncated[row,col]=True;break
                z[row,col,layer]=point.z;nz[row,col,layer]=normal.z;parts[row,col,layer]=groups[index];counts[row,col]+=1
                height=float(point.z-epsilon)
                if height<=bottom:break
        if row%20==0:print('Sampled rows',row+1,'/',len(ys),flush=True)
    np.savez_compressed(out/'vertical-surfaces.npz',x=xs,y=ys,z=z,normal_z=nz,part_index=parts,count=counts,truncated=truncated)
    samples={}
    for name,x,y in [('default_spawn',-8,-2),('red_spawn',8,2),('red_wave',7,6.4),('blue_wave',-7,-6.4),('centre',0,0)]:
        col=int(np.argmin(abs(xs-x)));row=int(np.argmin(abs(ys-y)));count=int(counts[row,col])
        samples[name]=dict(x=float(xs[col]),y=float(ys[row]),surfaces=[dict(z=float(z[row,col,k]),normal_z=float(nz[row,col,k]),part=part_names[parts[row,col,k]]) for k in range(count)])
    report=dict(status='geometry_sampled_not_navigation_validated',source_collision_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),descriptor_sha256=hashlib.sha256(descriptor.read_bytes()).hexdigest(),
        source_step_sha256=manifest['source_step_sha256'],coordinate_system='same ROS Z-up simulation frame as imported collision map',translation_m=translation.tolist(),
        bounds_m=[lo.tolist(),hi.tolist()],resolution_m=r,width=len(xs),height=len(ys),part_names=part_names,triangles=triangle_count,
        columns_with_geometry=int((counts>0).sum()),columns_with_more_than_two_surfaces=int((counts>2).sum()),truncated_columns=int(truncated.sum()),max_layers=max_layers,
        wall_seconds=time.monotonic()-start,samples=samples,limits=['Not an occupancy map or walkability proof','Needs support/clearance/connectivity and vehicle-envelope evaluation','Do not use a top surface image as traversability or AMCL map'])
    (out/'geometry-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('RMUC vertical geometry saved',len(xs),len(ys),'truncated',int(truncated.sum()),flush=True)

if __name__=='__main__':main()
