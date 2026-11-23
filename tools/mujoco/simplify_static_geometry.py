"""Blender: remove coplanar internal edges with an explicit geometry acceptance gate.

No decimation modifier, convex approximation, source asset mutation, or outer-edge
dissolution is used. Every boundary segment and bounds must remain unchanged.
"""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import sys
import time
import bmesh
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
sys.path.insert(0,str(Path(__file__).resolve().parent))
from geometry import digest, exact_cleanup

def boundary_edges(vertices,faces):
    edges=np.concatenate([faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]])
    edges=np.sort(edges,axis=1)
    unique,count=np.unique(edges,axis=0,return_counts=True)
    # Preserve non-manifold seams as well as open boundaries.
    edges=unique[count!=2]
    endpoints=vertices[edges].reshape(-1,6)
    if len(endpoints):
        swap=np.asarray([tuple(a)>tuple(b) for a,b in vertices[edges]],dtype=bool)
        endpoints[swap]=vertices[edges[swap,::-1]].reshape(-1,6)
        endpoints=endpoints[np.lexsort(endpoints.T[::-1])]
    return endpoints

def surface_area(v,f):
    return float(np.linalg.norm(np.cross(v[f[:,1]]-v[f[:,0]],v[f[:,2]]-v[f[:,0]]),axis=1).sum()/2)

def sampled_distance(source_v,source_f,target_v,target_f):
    # Both complete vertex sets and every triangle centroid are queried. Re-evaluate
    # point-to-plane distance in float64 after BVH's candidate selection.
    center=(target_v.min(axis=0)+target_v.max(axis=0))/2
    shifted=target_v-center
    tree=BVHTree.FromPolygons(shifted.tolist(),target_f.tolist(),all_triangles=True)
    points=np.concatenate([source_v,source_v[source_f].mean(axis=1)])
    maximum=0.;worst=None
    for offset in range(0,len(points),8192):
        p=points[offset:offset+8192]
        indices=[]
        for q in p:
            found=tree.find_nearest(Vector((q-center).tolist()))
            if found[2] is None:
                raise ValueError("Missing nearest source triangle")
            indices.append(found[2])
        tri=target_v[target_f[np.asarray(indices)]]
        # Standard Ericson closest point, vectorized over a batch.
        a,b,c=tri[:,0],tri[:,1],tri[:,2]
        ab,ac,ap=b-a,c-a,p-a
        d1=np.einsum("ij,ij->i",ab,ap);d2=np.einsum("ij,ij->i",ac,ap)
        bp=p-b;d3=np.einsum("ij,ij->i",ab,bp);d4=np.einsum("ij,ij->i",ac,bp)
        cp=p-c;d5=np.einsum("ij,ij->i",ab,cp);d6=np.einsum("ij,ij->i",ac,cp)
        va=d3*d6-d5*d4;vb=d5*d2-d1*d6;vc=d1*d4-d3*d2
        den=va+vb+vc
        nearest=a+ab*np.divide(vb,den,out=np.zeros_like(vb),where=den!=0)[:,None]+ac*np.divide(vc,den,out=np.zeros_like(vc),where=den!=0)[:,None]
        mask=(va<=0)&((d4-d3)>=0)&((d5-d6)>=0)
        t=np.divide(d4-d3,(d4-d3)+(d5-d6),out=np.zeros_like(d1),where=((d4-d3)+(d5-d6))!=0)
        nearest[mask]=b[mask]+(c-b)[mask]*t[mask,None]
        mask=(vb<=0)&(d2>=0)&(d6<=0)
        t=np.divide(d2,d2-d6,out=np.zeros_like(d2),where=(d2-d6)!=0)
        nearest[mask]=a[mask]+ac[mask]*t[mask,None]
        mask=(vc<=0)&(d1>=0)&(d3<=0)
        t=np.divide(d1,d1-d3,out=np.zeros_like(d1),where=(d1-d3)!=0)
        nearest[mask]=a[mask]+ab[mask]*t[mask,None]
        mask=(d1<=0)&(d2<=0);nearest[mask]=a[mask]
        mask=(d3>=0)&(d4<=d3);nearest[mask]=b[mask]
        mask=(d6>=0)&(d5<=d6);nearest[mask]=c[mask]
        dist=np.linalg.norm(p-nearest,axis=1)
        if float(dist.max(initial=0))>maximum:
            i=int(dist.argmax());maximum=float(dist[i]);worst=p[i].tolist()
    return {"maximum_m":maximum,"samples":len(points),"worst_sample":worst}

def process(item,source,output,angle,tolerance):
    file=source/item["file"]
    if digest(file)!=item["sha256"]:
        raise ValueError("Source geometry hash mismatch")
    with np.load(file,allow_pickle=False) as data:
        old_v,old_f=data["vertices"].copy(),data["triangles"].copy()
    start=time.perf_counter()
    mesh=bpy.data.meshes.new("MuJoCoCoplanar_"+item["id"])
    mesh.vertices.add(len(old_v));mesh.vertices.foreach_set("co",old_v.ravel())
    mesh.loops.add(old_f.size);mesh.loops.foreach_set("vertex_index",old_f.ravel())
    mesh.polygons.add(len(old_f));mesh.polygons.foreach_set("loop_start",np.arange(len(old_f),dtype=np.int32)*3)
    mesh.polygons.foreach_set("loop_total",np.full(len(old_f),3,dtype=np.int32));mesh.update()
    bm=bmesh.new();bm.from_mesh(mesh);bm.normal_update()
    bmesh.ops.dissolve_limit(bm,angle_limit=angle,verts=list(bm.verts),edges=list(bm.edges),
        use_dissolve_boundaries=False,delimit=set())
    bmesh.ops.triangulate(bm,faces=list(bm.faces),quad_method="BEAUTY",ngon_method="BEAUTY")
    bm.to_mesh(mesh);bm.free();mesh.update();mesh.calc_loop_triangles()
    vertices=np.empty((len(mesh.vertices),3),dtype=np.float32)
    mesh.vertices.foreach_get("co",vertices.ravel())
    faces=np.empty((len(mesh.loop_triangles),3),dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices",faces.ravel())
    vertices,faces,cleanup=exact_cleanup(vertices,faces)
    bpy.data.meshes.remove(mesh)
    before,after=boundary_edges(old_v,old_f),boundary_edges(vertices,faces)
    boundaries_equal=before.shape==after.shape and np.array_equal(before,after)
    bound_error=float(max(np.max(abs(old_v.min(axis=0)-vertices.min(axis=0))),np.max(abs(old_v.max(axis=0)-vertices.max(axis=0)))))
    source_distance=sampled_distance(old_v,old_f,vertices,faces)
    output_distance=sampled_distance(vertices,faces,old_v,old_f)
    old_area,new_area=surface_area(old_v,old_f),surface_area(vertices,faces)
    check={"boundary_segments_preserved":boundaries_equal,"boundary_segments_before":len(before),
        "boundary_segments_after":len(after),"bounds_max_error_m":bound_error,
        "source_to_result":source_distance,"result_to_source":output_distance,
        "source_area_m2":old_area,"result_area_m2":new_area,
        "relative_area_error":abs(new_area-old_area)/max(old_area,1e-30),
        "angle_limit_rad":angle,"surface_error_limit_m":tolerance,"seconds":time.perf_counter()-start}
    passed=boundaries_equal and bound_error<=tolerance and max(source_distance["maximum_m"],output_distance["maximum_m"])<=tolerance
    check["passed"]=passed
    if not passed:
        # Failed candidates remain separately available; accepted source geometry is retained.
        candidate=output/(item["id"]+".failed-candidate.npz")
        np.savez_compressed(candidate,vertices=vertices,triangles=faces)
        vertices,faces=old_v,old_f
    destination=output/item["file"]
    np.savez_compressed(destination,vertices=vertices,triangles=faces)
    result=copy.deepcopy(item)
    result.update(sha256=digest(destination),vertices=len(vertices),triangles=len(faces),
        coplanar_check=check,source_geometry_sha256=item["sha256"])
    print(json.dumps({"group":item["id"],"source_faces":len(old_f),"result_faces":len(faces),
        "accepted":passed,"seconds":check["seconds"],"bounds_error":bound_error,
        "surface_error":max(source_distance["maximum_m"],output_distance["maximum_m"]),
        "boundary_preserved":boundaries_equal}),flush=True)
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--angle",type=float,default=1e-7)
    p.add_argument("--tolerance",type=float,default=1e-6)
    p.add_argument("--groups",nargs="*")
    a=p.parse_args(sys.argv[sys.argv.index("--")+1:])
    a.output.mkdir(parents=True,exist_ok=False)
    report=json.loads((a.source/"geometry.json").read_text(encoding="utf-8-sig"))
    report["original_geometry_manifest_sha256"]=digest(a.source/"geometry.json")
    report["original_geometry_directory"]=str(a.source.resolve())
    report["method"]="guarded_coplanar_internal_edge_dissolution"
    report["blender_version"]=bpy.app.version_string
    report["groups"]=[process(i,a.source,a.output,a.angle,a.tolerance) for i in report["groups"] if not a.groups or i["id"] in a.groups]
    report["total_triangles"]=sum(i["triangles"] for i in report["groups"])
    report["total_vertices"]=sum(i["vertices"] for i in report["groups"])
    (a.output/"geometry.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"total_triangles":report["total_triangles"],"total_vertices":report["total_vertices"]}),flush=True)
if __name__=="__main__":main()
