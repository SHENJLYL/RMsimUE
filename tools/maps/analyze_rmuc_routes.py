"""Blender offline geometry evidence for manual-rig RMUC traversal (not UE acceptance).

Reads prepared original-size map and current FBX/rig manifest. Samples real terrain
wheel supports and compares the whole-box lower corners with the actual vehicle
vertex surface. It does not move actors or modify either source asset.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from io_scene_fbx import parse_fbx


def main():
    p=argparse.ArgumentParser();p.add_argument('--map',required=True);p.add_argument('--vehicle',required=True);p.add_argument('--output',required=True)
    a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
    manifest_path=Path(a.vehicle);rig=json.loads(manifest_path.read_text(encoding='utf-8'))
    bpy.ops.wm.open_mainfile(filepath=str(Path(a.map).resolve()))
    vertices=[];faces=[];groups=[];ramp_candidates=[];offset=0
    for obj in bpy.context.scene.objects:
        if obj.type!='MESH':continue
        mesh=obj.data;mesh.calc_loop_triangles()
        v=np.empty(len(mesh.vertices)*3,dtype=np.float32);mesh.vertices.foreach_get('co',v);v=v.reshape(-1,3)
        matrix=np.array(obj.matrix_world);v=v@matrix[:3,:3].T+matrix[:3,3]
        f=np.empty(len(mesh.loop_triangles)*3,dtype=np.int32);mesh.loop_triangles.foreach_get('vertices',f);f=f.reshape(-1,3)
        cross=np.cross(v[f[:,1]]-v[f[:,0]],v[f[:,2]]-v[f[:,0]]);norm=np.linalg.norm(cross,axis=1);normal=cross/np.maximum(norm[:,None],1e-16)
        selected=np.where((norm>.06)&(normal[:,2]>.819)&(normal[:,2]<.985))[0]
        for i in selected:
            ramp_candidates.append(dict(group=obj.name,center=v[f[i]].mean(axis=0).tolist(),normal=normal[i].tolist(),area=float(norm[i]/2)))
        vertices.extend(v.tolist());faces.extend((f+offset).tolist());groups.extend([obj.name]*len(f));offset+=len(v)
    print('Map triangles',len(faces),flush=True)
    terrain=BVHTree.FromPolygons(vertices,faces,all_triangles=True)
    del vertices,faces
    def ray(x,y,reference=0):
        point,normal,index,distance=terrain.ray_cast(Vector((x,y,reference+.4)),Vector((0,0,-1)),1.0)
        if point is None:return None
        return dict(point=list(point),normal=list(normal),group=groups[index])
    vehicle_points=[];origins={};part_bounds=[];wheels=[]
    def child(node,name):return next(c for c in node.elems if c.id==name)
    for part in rig['parts']:
        origin=np.array(part['origin']['xyz'])+(origins[part['parent']] if part['parent'] else 0)
        origins[part['name']]=origin
        root,version=parse_fbx.parse(str(manifest_path.parent/part['fbx']))
        geometry=next(e for e in child(root,b'Objects').elems if e.id==b'Geometry')
        points=np.array(child(geometry,b'Vertices').props[0],dtype=np.float64).reshape(-1,3)+origin
        vehicle_points.append(points)
        part_bounds.append(dict(name=part['name'],lo=points.min(axis=0).tolist(),hi=points.max(axis=0).tolist()))
        if part['name'].startswith('wheel'):wheels.append(origin)
    all_points=np.concatenate(vehicle_points);wheel_centers=np.array(wheels)
    lo,hi=all_points.min(axis=0),all_points.max(axis=0)
    radius=float(rig['wheel_radius_m']);gap=.02
    def pose(x,y,yaw,reference):
        fwd=np.array([math.cos(yaw),math.sin(yaw),0.]);side=np.array([-fwd[1],fwd[0],0.]);flat=np.stack([fwd,side,[0,0,1]],axis=1)
        wc=wheel_centers@flat.T+[x,y,0];hits=[ray(w[0],w[1],reference) for w in wc]
        if any(h is None for h in hits):return dict(ok=False,reason='no wheel support')
        contacts=np.array([h['point'] for h in hits]);A=np.stack([contacts[:,0],contacts[:,1],np.ones(len(contacts))],axis=1);coeff=np.linalg.lstsq(A,contacts[:,2],rcond=None)[0]
        normal=np.array([-coeff[0],-coeff[1],1]);normal/=np.linalg.norm(normal)
        fwd-=normal*np.dot(fwd,normal);fwd/=np.linalg.norm(fwd);side=np.cross(normal,fwd);rotation=np.stack([fwd,side,normal],axis=1)
        offsets=wheel_centers@rotation.T;surface=coeff[0]*(x+offsets[:,0])+coeff[1]*(y+offsets[:,1])+coeff[2]
        z=max(surface+radius/normal[2]+gap-offsets[:,2])
        # Whole solid bounding-box floor corners are exactly what the current C++ proxy claims.
        corners=np.array([[xx,yy,lo[2]] for xx in (lo[0],hi[0]) for yy in (lo[1],hi[1])])@rotation.T+[x,y,z]
        clearances=[]
        for c in corners:
            hit=ray(c[0],c[1],c[2]);clearances.append(None if hit is None else float(c[2]-hit['point'][2]))
        part_corner_min=[]
        for part in part_bounds:
            lower=np.array([[xx,yy,part['lo'][2]] for xx in (part['lo'][0],part['hi'][0]) for yy in (part['lo'][1],part['hi'][1])])@rotation.T+[x,y,z]
            margins=[]
            for c in lower:
                hit=ray(c[0],c[1],c[2]);
                if hit is not None:margins.append(float(c[2]-hit['point'][2]))
            part_corner_min.append(dict(part=part['name'],minimum_m=min(margins) if margins else None))
        return dict(ok=True,root=[x,y,z],rotation=rotation.tolist(),normal=normal.tolist(),slope_deg=math.degrees(math.acos(normal[2])),
            support_groups=[h['group'] for h in hits],support_heights=contacts[:,2].tolist(),box_floor_clearances=clearances,
            per_part_box_corner_clearances=part_corner_min,
            uneven_m=float(max(abs(A@coeff-contacts[:,2]))))
    routes=[]
    for label,start,direction,distance in [('default_forward',[-8,-2],0,4),('default_reverse',[-8,-2],math.pi,2),
        ('default_left',[-8,-2],math.pi/2,2),('default_right',[-8,-2],-math.pi/2,2)]:
        samples=[];reference=0
        for s in np.arange(0,distance+.001,.05):
            state=pose(start[0]+s*math.cos(direction),start[1]+s*math.sin(direction),direction,reference)
            if state['ok']:reference=state['root'][2]
            state['distance_m']=round(float(s),4);samples.append(state)
        routes.append(dict(name=label,samples=samples))
    rotations=[dict(yaw_deg=i,**pose(-8,-2,math.radians(i),0)) for i in range(0,360,5)]
    # Deduplicate source ramp planes, then traverse a short centerline uphill.
    seen=[];ramps=[]
    for candidate in sorted(ramp_candidates,key=lambda r:r['area'],reverse=True):
        point=np.array(candidate['center']);normal=np.array(candidate['normal'])
        if any(np.linalg.norm(point-c)<.9 and np.dot(normal,n)>.99 for c,n in seen):continue
        seen.append((point,normal));ascending=-normal[:2];ascending/=np.linalg.norm(ascending);yaw=math.atan2(ascending[1],ascending[0]);samples=[]
        reference=point[2]
        for s in np.arange(-.8,.801,.02):
            xy=point[:2]+ascending*s;state=pose(xy[0],xy[1],yaw,reference)
            if state['ok']:reference=state['root'][2]
            state['distance_from_center_m']=round(float(s),4);samples.append(state)
        counterexamples=[]
        for sample in samples:
            if not sample['ok'] or not any(c is not None and c<-.001 for c in sample['box_floor_clearances']):continue
            rotation=np.array(sample['rotation']);location=np.array(sample['root'])
            # If every actual mesh vertex is above an infinite ramp plane, all of its
            # triangles are above that plane too. This is stronger than a sampled hull
            # ray, while deliberately making no claim about other map obstacles.
            clearances=[]
            for part,points in zip(rig['parts'],vehicle_points):
                world=points@rotation.T+location
                clearance=(world-point)@normal
                clearances.append(dict(part=part['name'],minimum_signed_plane_distance_m=float(clearance.min())))
            actual_min=min(p['minimum_signed_plane_distance_m'] for p in clearances)
            if actual_min>=0:
                counterexamples.append(dict(root=sample['root'],distance_from_center_m=sample['distance_from_center_m'],
                    box_floor_clearances=sample['box_floor_clearances'],all_mesh_vertex_plane_clearance_m=actual_min,parts=clearances))
                if len(counterexamples)>=3:break
        ramps.append(dict(**candidate,yaw_rad=yaw,samples=samples,solid_box_false_positive_plane_evidence=counterexamples))
        if len(ramps)>=16:break
    report=dict(scope='offline real geometry wheel-support and solid-box corner diagnostics; requires native UE collision proof',
        input_files=[dict(path=str(Path(p).resolve()),sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest()) for p in
            [a.map,manifest_path,*[manifest_path.parent/part['fbx'] for part in rig['parts']]]],
        vehicle_dimensions=(hi-lo).tolist(),parts=part_bounds,wheel_centers=wheel_centers.tolist(),wheel_radius=radius,
        default_routes=routes,default_rotations=rotations,ramp_routes=ramps)
    (out/'analysis.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    summary=[]
    for route in routes:
        bad=[s for s in route['samples'] if not s['ok'] or any(c is not None and c<-.001 for c in s['box_floor_clearances'])]
        summary.append(dict(name=route['name'],first_proxy_problem=bad[0] if bad else None))
    print(json.dumps(dict(default_routes=summary,ramp_candidates=[{k:v for k,v in r.items() if k not in ('samples','solid_box_false_positive_plane_evidence')} for r in ramps],
        exact_plane_counterexamples=sum(len(r['solid_box_false_positive_plane_evidence']) for r in ramps)),indent=2),flush=True)


if __name__=='__main__':main()
