"""Physical inputs shared by model preparation and launch-time preflight.

Spawns, map display fields and arena lighting are outside the physical identity.
Vehicle parameters, collision assets, source geometry and boundaries are sealed.
"""
from __future__ import annotations
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

def digest(path,algorithm="sha256"):
    with Path(path).open("rb") as stream:return hashlib.file_digest(stream,algorithm).hexdigest()

def load_json(path):return json.loads(Path(path).read_text(encoding="utf-8-sig"))

def asset_file(repo,asset):
    if not asset.startswith("/Game/RMSim/") or ".." in asset:
        raise ValueError("Unexpected native collision asset path")
    return Path(repo)/"unreal/RMSim/Content"/(asset.split(".")[0].removeprefix("/Game/")+".uasset")

def map_files(repo,map_file=None):
    path=Path(map_file) if map_file else Path(repo)/"assets/maps/rmuc2027/map.json"
    return path,path.with_name("arena-shell.json")

def physical_map(repo,map_file=None):
    path,shell_path=map_files(repo,map_file)
    spec,shell=load_json(path),load_json(shell_path)
    for field,value in {"kind":"rm_mesh_map","units":"m",
            "coordinate_system":"ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP"}.items():
        if spec.get(field)!=value:raise ValueError("Unsupported physical map "+field)
    if shell.get("schema")!="rmsim_campus_gym_v1" or shell.get("map_id")!=spec["id"] or shell.get("units")!="m" or shell.get("coordinate_system")!=spec["coordinate_system"]:
        raise ValueError("Unsupported arena-shell physical identity")
    perimeter=shell["perimeter"]
    if perimeter.get("physics_contract")!="outside_platform_bounds_v1":
        raise ValueError("Unsupported arena boundary physics contract")
    clearance=float(perimeter["outside_clearance_m"])
    if not math.isfinite(clearance) or not 0<=clearance<=.02:
        raise ValueError("Invalid physical boundary clearance")
    if len({m["id"] for m in spec["meshes"]})!=len(spec["meshes"]):
        raise ValueError("Duplicate map collision IDs")
    return spec,shell

def physics_identity(repo,map_file=None):
    repo=Path(repo);spec,shell=physical_map(repo,map_file);boundary=shell["perimeter"]
    return {"map_id":spec["id"],"map_source_sha256":spec["source_sha256"],
        "collision_assets":[{"id":m["id"],"collision_asset":m["collision_asset"],
            "collision_uasset_sha256":digest(asset_file(repo,m["collision_asset"]))} for m in spec["meshes"]],
        "boundary":{"physics_contract":boundary["physics_contract"],
            "bounds_m":boundary.get("target_bounds_m",shell["bounds_m"]),
            "collision_bottom_m":boundary["collision_bottom_m"],
            "collision_top_m":boundary["collision_top_m"],
            "collision_thickness_m":boundary["collision_thickness_m"],
            "outside_clearance_m":boundary["outside_clearance_m"]}}

def identity_digest(identity):
    return hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def binding(path,role,repo,asset=None):
    path=Path(path).resolve()
    entry={"role":role,"path":str(path),"bytes":path.stat().st_size,
        "md5":digest(path,"md5"),"sha256":digest(path)}
    if asset is not None:entry["asset"]=asset
    try:entry["repository_path"]=str(path.relative_to(Path(repo).resolve())).replace("\\","/")
    except ValueError:pass
    return entry

def runtime_bindings(repo,manifest_dir,rig_file,profile_file,backend_file):
    repo=Path(repo);rig=load_json(rig_file)
    records=[binding(rig_file,"vehicle_rig",repo),binding(profile_file,"drive_profile",repo),
        binding(backend_file,"mujoco_backend",repo),binding(Path(manifest_dir)/"model.xml","model_xml",repo)]
    for p in rig["parts"]:
        records.append(binding(asset_file(repo,p["collision_asset"]),"vehicle_collision",repo,p["collision_asset"]))
    for p in load_json(repo/"assets/maps/rmuc2027/map.json")["meshes"]:
        records.append(binding(asset_file(repo,p["collision_asset"]),"map_collision",repo,p["collision_asset"]))
    return records

def check_sources(manifest,repo):
    """Check raw geometry provenance without reloading all million triangles."""
    checked=[]
    for name,expected_hash in manifest["source_sha256"].items():
        if name.startswith("tools/mujoco/"):
            path=repo/name
            if digest(path)!=expected_hash:raise ValueError("Model generator changed: "+name)
            checked.append(str(path))
    for record in manifest["vehicle_collision"]:
        path=repo/record["collision_record"]
        if digest(path)!=record["collision_record_sha256"]:
            raise ValueError("Source vehicle convex record changed: "+str(path))
        checked.append(str(path))
    if not manifest["diagnostic_plane_only"]:
        path=Path(manifest["geometry_manifest"])
        if digest(path)!=manifest["geometry_manifest_sha256"]:
            raise ValueError("Source geometry manifest changed")
        geometry=load_json(path)
        source=repo/geometry["source"]
        metadata=repo/"assets/maps/rmuc2027/mesh-source.json"
        if digest(source)!=geometry["source_sha256"]:
            raise ValueError("Source RMUC collision GLB changed")
        if digest(metadata)!=geometry["mesh_metadata_sha256"]:
            raise ValueError("Source RMUC geometry frame metadata changed")
        checked.extend(map(str,(path,source,metadata)))
        for item in geometry["groups"]:
            for field,hash_field in (("file","sha256"),("convex_file","convex_sha256")):
                if field not in item:continue
                payload=(path.parent/item[field]).resolve()
                if payload.parent!=path.parent.resolve() or digest(payload)!=item[hash_field]:
                    raise ValueError("Source geometry payload changed")
                checked.append(str(payload))
    return checked

def verify_manifest(manifest_file,repo,allow_diagnostic=False,map_file=None):
    manifest_file,repo=Path(manifest_file).resolve(),Path(repo).resolve()
    manifest=load_json(manifest_file)
    if manifest.get("schema")!="rmsim_mujoco_scene_v1":
        raise ValueError("Unsupported MuJoCo scene manifest")
    if manifest.get("status")!="compiled_pending_runtime_acceptance":
        raise ValueError("Model generation did not complete")
    if not isinstance(manifest.get("diagnostic_plane_only"),bool):
        raise ValueError("Model must declare its diagnostic scope")
    if manifest["diagnostic_plane_only"] and not allow_diagnostic:
        raise ValueError("Diagnostic flat-plane model requires explicit diagnostic opt-in")
    xml=Path(manifest["model_xml"])
    if xml.is_absolute() or ".." in xml.parts:
        raise ValueError("MuJoCo model must stay within its generated package")
    xml=(manifest_file.parent/xml).resolve()
    if not xml.is_relative_to(manifest_file.parent) or digest(xml)!=manifest["model_sha256"]:
        raise ValueError("MuJoCo XML byte identity changed")
    records=manifest.get("runtime_bindings")
    expected_counts={"vehicle_rig":1,"drive_profile":1,"mujoco_backend":1,
        "model_xml":1,"vehicle_collision":15,"map_collision":14}
    if not isinstance(records,list) or Counter(r.get("role") for r in records)!=expected_counts:
        raise ValueError("Expected sealed vehicle and map collision runtime bindings")
    actual_paths={"vehicle_rig":repo/"assets/vehicles/aim_sentry/ue_mesh_rig.json",
        "drive_profile":repo/"assets/vehicles/aim_sentry/drive-profile.json",
        "mujoco_backend":repo/"assets/vehicles/aim_sentry/mujoco/backend.json","model_xml":xml}
    rig=load_json(actual_paths["vehicle_rig"]);spec,_=physical_map(repo,map_file)
    expected_assets={"vehicle_collision":{p["collision_asset"] for p in rig["parts"]},
        "map_collision":{p["collision_asset"] for p in spec["meshes"]}}
    for role,assets in expected_assets.items():
        if {r.get("asset") for r in records if r["role"]==role}!=assets:
            raise ValueError("MuJoCo bound collision asset set changed: "+role)
    for record in records:
        role=record["role"]
        expected=actual_paths[role] if role in actual_paths else asset_file(repo,record["asset"])
        declared=Path(record["path"])
        if not declared.is_absolute():declared=manifest_file.parent/declared
        if declared.resolve()!=expected.resolve():
            raise ValueError("MuJoCo binding refers to a different active input: "+role)
        if expected.stat().st_size!=record["bytes"] or digest(expected,"md5")!=record["md5"] or digest(expected)!=record["sha256"]:
            raise ValueError("MuJoCo physical input changed: "+role+" "+str(expected))
    actual=physics_identity(repo,map_file)
    if actual!=manifest["physics_identity"] or identity_digest(actual)!=manifest["physics_identity_sha256"]:
        raise ValueError("Active RMUC physical map/boundary differs from the generated model")
    sources=check_sources(manifest,repo)
    return {"status":"passed","scope":"physical source identity and launch preflight only",
        "manifest":str(manifest_file),"manifest_sha256":digest(manifest_file),
        "model_xml":str(xml),"model_sha256":digest(xml),
        "physics_identity_sha256":identity_digest(actual),"runtime_binding_count":len(records),
        "source_file_count":len(sources),"diagnostic_plane_only":manifest["diagnostic_plane_only"],
        "mujoco_version":manifest["mujoco_version"],"timestep_s":manifest["timestep_s"]}
