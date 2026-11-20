"""Read-only source-to-MJCF terrain, boundary and current UE code audit."""
from __future__ import annotations
import argparse
import copy
import datetime
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import numpy as np
from model_contract import digest,load_json,verify_manifest
from prepare_model import add_static

def walls_from_xml(root):
    return {g.attrib["name"].removeprefix("arena_"):
        (np.fromstring(g.attrib["pos"],sep=" "),np.fromstring(g.attrib["size"],sep=" "))
        for g in root.find("worldbody").findall("geom") if g.attrib.get("name","").startswith("arena_")}

def check_wall_faces(walls,shell):
    p=shell["perimeter"];bounds=p["target_bounds_m"]
    lo,hi=np.array(bounds["lo"]),np.array(bounds["hi"])
    clearance=float(p["outside_clearance_m"]);thickness=float(p["collision_thickness_m"])
    errors=[];details=[]
    for name,(position,half) in walls.items():
        axis=0 if name in ("west","east") else 1
        sign=-1 if name in ("west","south") else 1
        target=(lo[axis] if sign<0 else hi[axis])+sign*clearance
        inner=position[axis]-sign*half[axis]
        thickness_error=abs(2*half[axis]-thickness)
        z_error=max(abs(position[2]-half[2]-p["collision_bottom_m"]),
            abs(position[2]+half[2]-p["collision_top_m"]))
        cross=1-axis
        # The other extent includes the full corner overlap beyond the inner face.
        edge_error=max(abs(position[cross]-half[cross]-(lo[cross]-clearance-thickness)),
            abs(position[cross]+half[cross]-(hi[cross]+clearance+thickness)))
        error=max(abs(inner-target),thickness_error,z_error,edge_error);errors.append(error)
        details.append({"name":name,"position_m":position.tolist(),"half_size_m":half.tolist(),
            "inner_face_m":float(inner),"expected_inner_face_m":float(target),"max_error_m":float(error)})
    if set(walls)!={"west","east","south","north"} or max(errors)>1e-12:
        raise ValueError("Boundary boxes disagree with the current native UE perimeter envelope")
    return details

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo",type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument("--manifest",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args();repo=a.repo.resolve();output=a.output.resolve();output.mkdir(parents=True,exist_ok=False)
    manifest=load_json(a.manifest);preflight=verify_manifest(a.manifest,repo)
    geometry_path=Path(manifest["geometry_manifest"]);geometry=load_json(geometry_path)
    metadata=load_json(repo/"assets/maps/rmuc2027/mesh-source.json")
    metadata_groups={g["id"]:g for g in metadata["collisions"]}
    shell=load_json(repo/"assets/maps/rmuc2027/arena-shell.json")
    root=ET.parse(a.manifest.resolve().parent/manifest["model_xml"]).getroot()
    flexes={f.attrib["name"].removeprefix("terrain_"):f for f in root.find("deformable").findall("flex")}
    if set(flexes)!={g["id"] for g in geometry["groups"]}:raise ValueError("Incomplete or extra terrain flex")
    results=[];anchors=[];total_vertices=total_triangles=0
    for item in geometry["groups"]:
        f=flexes[item["id"]]
        if f.attrib.get("body")!="world" or f.attrib.get("dim")!="2":
            raise ValueError("Terrain must remain a fixed world surface")
        points=np.fromstring(f.attrib["vertex"],sep=" ").reshape(-1,3)
        faces=np.fromstring(f.attrib["element"],sep=" ",dtype=np.int32).reshape(-1,3)
        with np.load(geometry_path.parent/item["file"],allow_pickle=False) as payload:
            source_points=payload["vertices"];source_faces=payload["triangles"]
            exact_points=np.array_equal(points,source_points);exact_faces=np.array_equal(faces,source_faces)
            max_error=float(np.max(np.abs(points-source_points))) if points.shape==source_points.shape else None
        if not exact_points or not exact_faces:
            raise ValueError("Source terrain geometry changed during MJCF serialization: "+item["id"])
        bounds={"lo":points.min(axis=0).tolist(),"hi":points.max(axis=0).tolist()}
        metadata_error=max(abs(bounds[k][i]-metadata_groups[item["id"]]["bounds_m"][k][i])
            for k in ("lo","hi") for i in range(3))
        if metadata_error>1e-6:raise ValueError("Source import coordinate/bounds mismatch")
        result={"map_mesh":item["id"],"vertices":len(points),"triangles":len(faces),
            "vertices_exact":exact_points,"indices_exact":exact_faces,"max_serialization_error_m":max_error,
            "source_metadata_bounds_error_m":metadata_error,"bounds_m":bounds,"flex_radius_m":float(f.attrib["radius"])}
        results.append(result);total_vertices+=len(points);total_triangles+=len(faces)
        for side,key in (("south","lo"),("north","hi")):
            if shell["perimeter"]["platform_reference_meshes"][side]==item["id"]:
                target=shell["perimeter"]["target_bounds_m"][key][1]
                anchors.append({"side":side,"mesh":item["id"],"source_edge_y_m":bounds[key][1],
                    "actual_saved_native_anchor_y_m":target,"source_to_native_anchor_difference_m":bounds[key][1]-target})
    walls=check_wall_faces(walls_from_xml(root),shell)
    clearance_checks=[]
    config=load_json(repo/"assets/vehicles/aim_sentry/mujoco/backend.json")
    for clearance in (0.0,.001,.02):
        fixture=copy.deepcopy(shell);fixture["perimeter"]["outside_clearance_m"]=clearance
        test_root=ET.Element("mujoco");ET.SubElement(test_root,"worldbody");ET.SubElement(test_root,"asset")
        add_static(test_root,[],fixture,config)
        details=check_wall_faces(walls_from_xml(test_root),fixture)
        clearance_checks.append({"clearance_m":clearance,"max_error_m":max(x["max_error_m"] for x in details)})
    review=load_json(Path(__file__).with_name("native-static-review.json"))
    for name,expected in review["source_sha256"].items():
        if digest(repo/name)!=expected:
            raise ValueError("Native static construction changed; repeat the source review: "+name)
    source_files=["unreal/RMSim/Source/RMSim/MapStage.cpp","unreal/RMSim/Source/RMSim/MeshMap.cpp",
        "unreal/RMSim/Source/RMSim/ArenaShell.cpp","unreal/RMSim/Source/RMSim/CampusGym.cpp",
        "unreal/RMSim/Source/RMSim/ImportLabGameMode.cpp","unreal/RMSim/Source/RMSim/TrainingRange.cpp",
        "assets/maps/rmuc2027/map.json","assets/maps/rmuc2027/arena-shell.json"]
    report={"schema":"rmsim_mujoco_static_scene_audit_v1","status":"passed",
        "created_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"command":sys.argv,
        "scope":"Read-only original source payload to MJCF equality, current native collider construction and boundary dimensions; no new UE session",
        "auditor_sha256":digest(Path(__file__)),"native_code_review":review,
        "preflight":preflight,"vertices":total_vertices,"triangles":total_triangles,"terrain_flexes":len(results),
        "boundary_boxes":walls,"terrain":results,"source_to_saved_native_anchors":anchors,
        "clearance_formula_checks":clearance_checks,
        "source_sha256":{name:digest(repo/name) for name in source_files},
        "native_code_findings":[
            "MapStage::SpawnGeometry creates one identity-transform CTF_UseComplexAsSimple QueryAndPhysics actor for each of the 14 map collision assets.",
            "The paired 14 visual meshes use NoCollision; the mesh-map branch returns before primitive floor/wall generation.",
            "CampusGym::NewGroup sets every instanced appearance component to NoCollision, including school exterior, sky, distant ground, parquet and visible glass.",
            "CampusGym::Spawn creates exactly four hidden QueryAndPhysics arena_guard actors using the perimeter dimensions checked above.",
            "TrainingRange stands and armor are optional QueryOnly objects; they do not add rigid-body physical obstacles.",
            "The accepted triangle index arrays and vertex coordinates are identical to the complete source cache; source holes, concavities, ramps and waves are preserved."
        ],
        "limits":[
            "The scene-code audit covers current constructed RMUC actors. Actual loaded-world registration and native contacts remain the UE integration acceptance.",
            "Source-to-native FBX float conversion can produce micrometre-scale differences; the recorded native platform anchors are used for perimeter faces.",
            "Terrain uses the explicitly configured 0.1 mm contact shell radius; material/contact calibration is separate from geometric equality."
        ]}
    (output/"report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":"passed","terrain_flexes":len(results),"vertices":total_vertices,
        "triangles":total_triangles,"boundary_boxes":len(walls),"max_vertex_error_m":max(x["max_serialization_error_m"] for x in results),
        "source_to_native_anchors":anchors,"clearance_formula_checks":clearance_checks}),flush=True)

if __name__=="__main__":main()
