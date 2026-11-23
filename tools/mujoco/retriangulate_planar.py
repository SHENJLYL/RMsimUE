"""Generate coplanar union/CDT candidates, preserving polygon holes.

Candidates require independent bidirectional surface verification before use.
"""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import time
import numpy as np
import shapely
from geometry import digest,exact_cleanup

def process(item,source,output):
    start=time.perf_counter()
    file=source/item["file"]
    if digest(file)!=item["sha256"]:raise ValueError("Source geometry identity changed")
    with np.load(file,allow_pickle=False) as data:v,f=data["vertices"],data["triangles"]
    triangles=v[f]
    normals=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
    lengths=np.linalg.norm(normals,axis=1)
    normals/=lengths[:,None]
    dominant=np.argmax(abs(normals),axis=1)
    sign=np.sign(normals[np.arange(len(normals)),dominant])
    canonical=normals*sign[:,None]
    distances=-np.einsum("ij,ij->i",canonical,triangles[:,0])
    # These are grouping bins only. Every group also has a direct plane-distance gate.
    keys=np.round(np.c_[canonical,distances],10)
    _,labels=np.unique(keys,axis=0,return_inverse=True)
    order=np.argsort(labels,kind="stable");cuts=np.flatnonzero(np.diff(labels[order]))+1
    groups=np.split(order,cuts)
    retained=[];extra=[];changes=[];failed=0
    for ids in groups:
        if len(ids)<=2:
            retained.append(ids);continue
        n=canonical[ids[0]];d=float(distances[ids[0]])
        region=triangles[ids]
        error=float(np.max(abs(region@n+d)))
        if error>1e-9:
            retained.append(ids);continue
        drop=int(abs(n).argmax());axes=[i for i in range(3) if i!=drop]
        twod=region[:,:,axes]
        try:
            polygons=shapely.polygons(twod)
            union=shapely.union_all(polygons)
            if union.is_empty or not union.is_valid:
                retained.append(ids);failed+=1;continue
            result=shapely.constrained_delaunay_triangles(union)
            pieces=list(shapely.get_parts(result))
            if not pieces or len(pieces)>=len(ids):
                retained.append(ids);continue
            points=np.asarray([shapely.get_coordinates(poly)[:3] for poly in pieces])
            world=np.empty((len(points),3,3),dtype=float)
            world[:,:,axes]=points
            world[:,:,drop]=(-d-np.sum(points*n[axes],axis=2))/n[drop]
            # Preserve the majority original orientation; static surfaces are double sided.
            for i in range(len(world)):
                if np.dot(np.cross(world[i,1]-world[i,0],world[i,2]-world[i,0]),n)<0:
                    world[i]=world[i,[0,2,1]]
            if float(np.mean(sign[ids]))<0:world=world[:,[0,2,1]]
            extra.append(world)
            changes.append({"source_triangles":len(ids),"result_triangles":len(world),
                "maximum_source_plane_distance_m":error,"plane_normal":n.tolist(),"plane_offset":d,
                "polygons":len(list(shapely.get_parts(union))),
                "holes":sum(len(poly.interiors) for poly in shapely.get_parts(union) if poly.geom_type=="Polygon"),
                "source_projected_area":float(shapely.area(polygons).sum()),"union_projected_area":float(union.area)})
        except Exception:
            retained.append(ids);failed+=1
    remaining=triangles[np.concatenate(retained)] if retained else np.empty((0,3,3))
    all_triangles=np.concatenate([remaining,*extra])
    raw_vertices=all_triangles.reshape(-1,3)
    raw_faces=np.arange(len(raw_vertices),dtype=np.int32).reshape(-1,3)
    vertices,faces,cleanup=exact_cleanup(raw_vertices,raw_faces)
    file=output/item["file"];np.savez_compressed(file,vertices=vertices,triangles=faces)
    out=copy.deepcopy(item)
    out.update(sha256=digest(file),vertices=len(vertices),triangles=len(faces),
        previous_geometry_sha256=item["sha256"],
        planar_candidate={"status":"pending_independent_surface_verification","changes":changes,
            "failed_groups_kept_original":failed,"plane_grouping_decimals":10,
            "maximum_plane_error_m":1e-9,"seconds":time.perf_counter()-start})
    print(json.dumps({"group":item["id"],"source_triangles":len(f),"candidate_triangles":len(faces),
        "changed_plane_groups":len(changes),"seconds":out["planar_candidate"]["seconds"]}),flush=True)
    return out

def main():
    p=argparse.ArgumentParser();p.add_argument("--source",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    p.add_argument("--groups",nargs="*");a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    report=json.loads((a.source/"geometry.json").read_text(encoding="utf-8-sig"))
    report.update(method="coplanar_polygon_union_and_constrained_delaunay_candidate",
        status="pending_independent_surface_verification",shapely_version=shapely.__version__,
        previous_geometry_manifest_sha256=digest(a.source/"geometry.json"),
        previous_geometry_directory=str(a.source.resolve()))
    report["groups"]=[process(i,a.source,a.output) for i in report["groups"] if not a.groups or i["id"] in a.groups]
    report["total_triangles"]=sum(i["triangles"] for i in report["groups"])
    report["total_vertices"]=sum(i["vertices"] for i in report["groups"])
    (a.output/"geometry.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"triangles":report["total_triangles"],"vertices":report["total_vertices"]}),flush=True)
if __name__=="__main__":main()
