"""Preserve static surface geometry while separating provably closed convex pieces."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import time
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import ConvexHull,QhullError
from geometry import digest,exact_cleanup

def components(vertices,faces):
    edges=np.concatenate([faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]])
    graph=coo_matrix((np.ones(len(edges),dtype=np.int8),(edges[:,0],edges[:,1])),
        shape=(len(vertices),len(vertices))).tocsr()
    count,labels=connected_components(graph,directed=False)
    face_labels=labels[faces[:,0]]
    order=np.argsort(face_labels,kind="stable")
    cuts=np.flatnonzero(np.diff(face_labels[order]))+1
    return np.split(order,cuts)

def closed_consistent(faces):
    edges=np.concatenate([faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]])
    key=np.sort(edges,axis=1)
    unique,inv,counts=np.unique(key,axis=0,return_inverse=True,return_counts=True)
    if np.any(counts!=2):
        return False
    signed=np.where(edges[:,0]<edges[:,1],1,-1)
    return bool(np.all(np.bincount(inv,weights=signed)==0))

def process(item,source,output):
    start=time.perf_counter()
    path=source/item["file"]
    if digest(path)!=item["sha256"]:raise ValueError("Source hash mismatch")
    data=np.load(path,allow_pickle=False);v,f=data["vertices"],data["triangles"]
    hulls=[];retained=[];records=[];rejections={}
    comps=components(v,f)
    for ci,face_ids in enumerate(comps):
        local_f=f[face_ids]
        ids,inverse=np.unique(local_f.ravel(),return_inverse=True)
        points=v[ids]
        faces=inverse.reshape(-1,3)
        reason=None
        if len(faces)<4 or len(points)<4:reason="not_volume"
        elif not closed_consistent(faces):reason="open_or_inconsistent"
        else:
            center=(points.min(axis=0)+points.max(axis=0))/2
            p=points-center
            try:hull=ConvexHull(p)
            except QhullError:reason="non_volume_hull"
            if reason is None:
                tri=p[faces]
                signed=float(np.einsum("ij,ij->i",tri[:,0],np.cross(tri[:,1],tri[:,2])).sum()/6)
                err=abs(abs(signed)-hull.volume)
                if hull.volume<=1e-18 or err>max(hull.volume*1e-9,1e-18):
                    reason="concave_volume"
                else:
                    # For a convex closed surface all original face centers lie on
                    # the hull boundary; test every center against every hull plane.
                    maximum_inset=0.
                    eq=hull.equations
                    for offset in range(0,len(faces),128):
                        centers=tri[offset:offset+128].mean(axis=1)
                        signed_distance=centers@eq[:,:3].T+eq[:,3]
                        maximum_inset=max(maximum_inset,float(np.max(-signed_distance.max(axis=1),initial=0)))
                    if maximum_inset>1e-8:reason="non_boundary_face"
                    else:
                        # Original extreme points only. No fitting, inflation or decimation.
                        hp=points[hull.vertices]
                        index=len(hulls);hulls.append(hp)
                        records.append({"component":ci,"hull_index":index,"source_faces":len(faces),
                            "source_vertices":len(points),"hull_vertices":len(hp),"source_volume_m3":abs(signed),
                            "hull_volume_m3":float(hull.volume),"volume_error_m3":err,
                            "all_face_center_boundary_error_m":maximum_inset})
        if reason is not None:
            retained.append(face_ids);rejections[reason]=rejections.get(reason,0)+1
    remainder=f[np.concatenate(retained)] if retained else np.empty((0,3),dtype=np.int32)
    if len(remainder):rv,rf,_=exact_cleanup(v,remainder)
    else:rv=np.empty((0,3));rf=np.empty((0,3),dtype=np.int32)
    destination=output/item["file"];np.savez_compressed(destination,vertices=rv,triangles=rf)
    hull_file=output/(item["id"]+".convex.npz")
    offsets=np.r_[0,np.cumsum([len(h) for h in hulls])]
    np.savez_compressed(hull_file,vertices=np.concatenate(hulls) if hulls else np.empty((0,3)),offsets=offsets)
    result=copy.deepcopy(item)
    result.update(file=destination.name,sha256=digest(destination),vertices=len(rv),triangles=len(rf),
        source_geometry_sha256=item["sha256"],convex_file=hull_file.name,convex_sha256=digest(hull_file),
        convex_count=len(hulls),convex_vertices=sum(map(len,hulls)),
        convex_audit={"components":len(comps),"closed_convex":records,"rejections":rejections,"seconds":time.perf_counter()-start})
    print(json.dumps({"group":item["id"],"components":len(comps),"convex":len(hulls),
        "source_faces":len(f),"residual_flex_faces":len(rf),"seconds":result["convex_audit"]["seconds"]}),flush=True)
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument("--source",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    p.add_argument("--groups",nargs="*")
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    report=json.loads((a.source/"geometry.json").read_text(encoding="utf-8-sig"))
    report["previous_geometry_manifest_sha256"]=digest(a.source/"geometry.json")
    report["previous_geometry_directory"]=str(a.source.resolve())
    report["method"]="closed_consistent_convex_components_plus_exact_residual_surface"
    report["groups"]=[process(i,a.source,a.output) for i in report["groups"] if not a.groups or i["id"] in a.groups]
    report["total_triangles"]=sum(i["triangles"] for i in report["groups"])
    report["total_vertices"]=sum(i["vertices"] for i in report["groups"])
    report["total_convex"]=sum(i["convex_count"] for i in report["groups"])
    (a.output/"geometry.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"total_triangles":report["total_triangles"],"total_convex":report["total_convex"]}),flush=True)
if __name__=="__main__":main()
