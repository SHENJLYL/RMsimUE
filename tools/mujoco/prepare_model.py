"""Build a source-bound MuJoCo model for the existing AIM/UE scene.

Use the isolated, pinned MuJoCo Python environment. Generated XML/geometry stays
local. The rig, its physical profile, all existing UE assets and CAD stay read-only.
"""
from __future__ import annotations
import argparse
import datetime
import itertools
import json
import math
from pathlib import Path
import time
import xml.etree.ElementTree as ET
import numpy as np
import mujoco
from geometry import digest, extract_rmuc, load_geometry
from model_contract import physical_map, physics_identity, identity_digest, runtime_bindings

def numbers(values):
    return " ".join(format(float(x), ".17g") for x in np.asarray(values).ravel())

def integers(values):
    return " ".join(str(int(x)) for x in np.asarray(values).ravel())

def sub(parent, tag, **attributes):
    return ET.SubElement(parent,tag,{k:str(v) for k,v in attributes.items()})

def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))

def relative(path, repo):
    return str(Path(path).resolve().relative_to(Path(repo).resolve())).replace("\\","/")

def find_collision_records(repo, rig):
    wanted = {part["collision_asset_sha256"]:part["name"] for part in rig["parts"]}
    matches = {}
    for path in sorted((repo/"artifacts/assets/contact-physics").rglob("*.json")):
        if path.name.startswith("rig-"):
            continue
        try:
            record = load_json(path)
        except (ValueError,OSError):
            continue
        if not isinstance(record,dict):
            continue
        for part in record.get("parts",[]):
            if not isinstance(part,dict) or not part.get("hulls"):
                continue
            sha = part.get("collision_uasset_sha256")
            if sha in wanted and part.get("name")==wanted[sha]:
                matches[wanted[sha]]=(path,part)
    if set(matches)!={part["name"] for part in rig["parts"]}:
        raise ValueError("Hash-matching native convex records are missing")
    hulls,provenance={},[]
    for part in rig["parts"]:
        path,record = matches[part["name"]]
        native=repo/"unreal/RMSim/Content"/(part["collision_asset"].split(".")[0].removeprefix("/Game/")+".uasset")
        if digest(native)!=part["collision_asset_sha256"]:
            raise ValueError("Saved native collision asset has changed: "+part["name"])
        if record["source_fbx_sha256"]!=part["source_fbx_sha256"]:
            raise ValueError("Collision record/source FBX mismatch")
        points=[]
        for hull in record["hulls"]:
            p=np.asarray(hull["vertices"],dtype=float)*np.array([.01,-.01,.01])
            if p.ndim!=2 or p.shape[1]!=3 or len(p)<4 or not np.isfinite(p).all():
                raise ValueError("Invalid native convex hull")
            points.append(p)
        hulls[part["name"]]=points
        provenance.append({"part":part["name"],"collision_asset":part["collision_asset"],
            "collision_asset_sha256":digest(native),"collision_record":relative(path,repo),
            "collision_record_sha256":digest(path),"source_fbx_sha256":part["source_fbx_sha256"],
            "hulls":len(points),"vertices":sum(map(len,points))})
    return hulls,provenance

def masses_for(rig,profile,config):
    p=profile["physics"]
    masses={}
    for part in rig["parts"]:
        name=part["name"]
        if name=="body":
            mass=p["ChassisMassKg"]  # UE welded yaw/pitch masses remain additional, as measured at runtime.
        elif name in ("yaw","pitch"):
            mass=config["gimbal_mass_kg"][name]
        elif name.startswith("wheel_"):
            mass=p["WheelMassKg"]
        elif name.startswith("suspension_"):
            mass=p["SliderMassKg"]
        elif name.startswith("steering_"):
            mass=p["SteeringMassKg"]
        else:
            raise ValueError("Unmapped rig part "+name)
        if not math.isfinite(mass) or mass<=0:
            raise ValueError("Invalid requested body mass")
        masses[name]=mass
    return masses

def source_average_friction(profile,config,is_wheel):
    """Match the current UE vehicle Average rule against the verified static material.

    A higher vehicle priority selects the precombined coefficient for static flex
    and static geom contacts. All solver parameters remain identical on both sides.
    """
    mapping=config.get("contact_mapping",{})
    cc=config["contact"]
    if (mapping.get("schema")!="rmsim_ue_homogeneous_static_contact_v1" or
            mapping.get("source_friction_combine")!="Average" or
            mapping.get("source_restitution_combine")!="Min" or
            mapping.get("vehicle_priority")!=1 or mapping.get("static_priority")!=0 or
            mapping.get("static_friction_binary32")!=cc["static_sliding_friction"] or
            cc["condim"]!=3 or cc["torsional_friction"]!=0 or cc["rolling_friction"]!=0):
        raise ValueError("Unsupported source contact mapping; inspect actual UE materials before generation")
    key="WheelFriction" if is_wheel else "ChassisFriction"
    source_vehicle=float(np.float32(profile["physics"][key]))
    source_static=cc["static_sliding_friction"]
    if not np.isfinite(source_vehicle) or not np.isfinite(source_static) or min(source_vehicle,source_static)<0:
        raise ValueError("Invalid source friction coefficient")
    return .5*(source_vehicle+source_static)


def vehicle_xml(rig,profile,config,hulls,spawn,inertias=None):
    root=ET.Element("mujoco",model="RMSim_aim_sentry")
    sub(root,"compiler",angle="radian",autolimits="true",inertiafromgeom="true" if inertias is None else "false")
    sub(root,"option",timestep=config["timestep_s"],integrator=config["integrator"],
        solver=config["solver"],iterations=config["iterations"],cone=config["cone"],gravity=numbers(config["gravity_mps2"]))
    sub(root,"size",memory="256M")
    asset=sub(root,"asset")
    world=sub(root,"worldbody")
    contact=sub(root,"contact")
    act=sub(root,"actuator")
    c,p=profile["control"],profile["physics"]
    parts={x["name"]:x for x in rig["parts"]}
    wheels={x["part"]:x for x in rig["wheels"]}
    bodies={}
    for part in rig["parts"]:
        name=part["name"]
        if part["origin"]["rpy"]!=[0,0,0]:
            raise ValueError("Current native rig contract requires source-aligned zero rest rotation")
        parent=world if part["parent"] is None else bodies[part["parent"]]
        position=np.asarray(part["origin"]["xyz"],dtype=float)
        if name=="body":
            position+=spawn["root_position_m"]
        body=sub(parent,"body",name=name,pos=numbers(position))
        bodies[name]=body
        if name=="body":
            body.set("quat",numbers(spawn["root_quaternion_wxyz"]))
            sub(body,"freejoint",name="chassis_free")
        elif name.startswith("suspension_"):
            sub(body,"joint",name="j_"+name,type="slide",axis="0 0 1",
                limited="true",range=numbers([-.005*p["SuspensionTravelCm"],.005*p["SuspensionTravelCm"]]))
            # Passive spring law with the same preload and force limit as the UE profile.
            sub(act,"general",name="a_spring_"+name,joint="j_"+name,
                gainprm="1",biastype="affine",
                biasprm=numbers([-p["SpringPreloadN"],-p["SpringStiffnessNPerM"],-p["SpringDampingNsPerM"]]),
                forcelimited="true",forcerange=numbers([-p["SpringForceLimitN"],p["SpringForceLimitN"]]))
        else:
            axis=rig["gimbal"][name]["axis"] if name in ("yaw","pitch") else (
                wheels[name]["axis"] if name in wheels else [0,0,1])
            joint=sub(body,"joint",name="j_"+name,type="hinge",axis=numbers(axis),limited="false")
            if name=="pitch":
                joint.set("limited","true")
                joint.set("range",numbers([rig["gimbal"]["pitch"]["lower"],rig["gimbal"]["pitch"]["upper"]]))
            if name in wheels:
                kp,kd,limit=0.,p["MotorDampingNmS"],p["MotorTorqueNm"]
            elif name.startswith("steering_"):
                kp,kd,limit=c["SteeringStiffnessNmRad"],c["SteeringDampingNmS"],c["SteeringTorqueNm"]
            else:
                g=config["gimbal_control"]
                kp,kd,limit=g["stiffness_nm_rad"],g["damping_nm_s"],g["max_torque_nm"]
            sub(act,"general",name="a_"+name,joint="j_"+name,gainprm="1",biastype="affine",
                biasprm=numbers([0,-kp,-kd]),forcelimited="true",forcerange=numbers([-limit,limit]))
        if inertias is not None:
            i=inertias[name]
            sub(body,"inertial",mass=i["mass_kg"],pos=numbers(i["position_m"]),
                quat=numbers(i["quaternion_wxyz"]),diaginertia=numbers(i["principal_inertia_kg_m2"]))
        for index,points in enumerate(hulls[name]):
            mesh_name=f"m_{name}_{index}"
            sub(asset,"mesh",name=mesh_name,vertex=numbers(points))
            friction=source_average_friction(profile,config,name in wheels)
            cc=config["contact"]
            sub(body,"geom",name=f"g_{name}_{index}",type="mesh",mesh=mesh_name,
                density="1000" if inertias is None else "0",contype="1",conaffinity="3",priority="1",
                friction=numbers([friction,cc["torsional_friction"],cc["rolling_friction"]]),
                condim=cc["condim"],solref=numbers(cc["solref"]),solimp=numbers(cc["solimp"]),
                rgba="0.3 0.4 0.6 1",group="1")
    # Only intra-vehicle exclusions. A second vehicle retains inter-vehicle collisions.
    for first,second in itertools.combinations(parts,2):
        sub(contact,"exclude",body1=first,body2=second)
    return root

def compute_inertias(rig,profile,config,hulls):
    spawn={"root_position_m":[0,0,1],"root_quaternion_wxyz":[1,0,0,0]}
    xml=ET.tostring(vehicle_xml(rig,profile,config,hulls,spawn),encoding="unicode")
    model=mujoco.MjModel.from_xml_string(xml)
    masses=masses_for(rig,profile,config)
    result={}
    for name,mass in masses.items():
        i=mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_BODY,name)
        if i<0 or model.body_mass[i]<=0:
            raise ValueError("Cannot infer positive source body inertia: "+name)
        scale=mass/float(model.body_mass[i])
        result[name]={"mass_kg":mass,"position_m":model.body_ipos[i].tolist(),
            "quaternion_wxyz":model.body_iquat[i].tolist(),
            "principal_inertia_kg_m2":(model.body_inertia[i]*scale).tolist()}
    return result

def height_at(groups,x,y,upper):
    answer=None
    # Barycentric vertical query on the same source triangles, independent of MuJoCo.
    query=[]
    for item,points,faces in groups:
        if len(faces):query.append((points,faces))
        if item.get("_convex_hulls"):
            from scipy.spatial import ConvexHull
            query.extend((v,ConvexHull(v).simplices) for v in item["_convex_hulls"])
    for points,faces in query:
        a,b,c=(points[faces[:,k]] for k in range(3))
        lo=np.minimum(np.minimum(a[:,:2],b[:,:2]),c[:,:2])
        hi=np.maximum(np.maximum(a[:,:2],b[:,:2]),c[:,:2])
        keep=(lo[:,0]<=x)&(hi[:,0]>=x)&(lo[:,1]<=y)&(hi[:,1]>=y)
        a,b,c=a[keep],b[keep],c[keep]
        if not len(a):continue
        den=(b[:,1]-c[:,1])*(a[:,0]-c[:,0])+(c[:,0]-b[:,0])*(a[:,1]-c[:,1])
        good=np.abs(den)>1e-15
        a,b,c,den=a[good],b[good],c[good],den[good]
        u=((b[:,1]-c[:,1])*(x-c[:,0])+(c[:,0]-b[:,0])*(y-c[:,1]))/den
        v=((c[:,1]-a[:,1])*(x-c[:,0])+(a[:,0]-c[:,0])*(y-c[:,1]))/den
        w=1-u-v
        z=u*a[:,2]+v*b[:,2]+w*c[:,2]
        valid=(u>=-1e-9)&(v>=-1e-9)&(w>=-1e-9)&(z<=upper)
        if valid.any():
            candidate=float(z[valid].max())
            answer=candidate if answer is None else max(answer,candidate)
    if answer is None:
        raise ValueError("Spawn has no original triangle support")
    return answer

def spawn_for(map_spec,rig,groups,name,clearance):
    source=next((s for s in map_spec["spawns"] if s["id"]==name),None)
    if source is None:
        raise ValueError("Unknown declared spawn")
    x,y,reference=source["position_m"]
    yaw=float(source["yaw_rad"])
    rotation=np.array([[math.cos(yaw),-math.sin(yaw)],[math.sin(yaw),math.cos(yaw)]])
    roots=[]
    for suspension in rig["suspension"]:
        wheel=next(w for w in rig["wheels"] if w["part"]==suspension["wheel_part"])
        rest=np.asarray(suspension["rest_position_m"])
        xy=rotation@rest[:2]+[x,y]
        height=height_at(groups,*xy,upper=reference+.5)
        roots.append(height+wheel["radius_m"]-rest[2])
    return {"id":name,"declared_spawn":source,"root_position_m":[x,y,max(roots)+clearance],
        "root_quaternion_wxyz":[math.cos(yaw/2),0,0,math.sin(yaw/2)],
        "initial_clearance_m":clearance,"wheel_support_root_heights_m":roots}

def add_static(root,groups,shell,config):
    world=root.find("worldbody")
    deform=sub(root,"deformable")
    cc=config["contact"]
    mappings=[]
    for item,points,faces in groups:
        name="terrain_"+item["id"]
        if len(faces):
            flex=sub(deform,"flex",name=name,body="world",dim="2",radius=cc["flex_radius_m"],
                vertex=numbers(points),element=integers(faces))
            sub(flex,"contact",selfcollide="none",contype="2",conaffinity="1",priority="0",condim=cc["condim"],
                friction=numbers([cc["static_sliding_friction"],cc["torsional_friction"],cc["rolling_friction"]]),
                solref=numbers(cc["solref"]),solimp=numbers(cc["solimp"]))
            mappings.append({"name":name,"kind":"static_flex","map_mesh":item["id"],"triangles":len(faces),
                "source_geometry_sha256":item["sha256"]})
        for i,vertices in enumerate(item.get("_convex_hulls",[])):
            cname=f"{name}_component_{i}"
            sub(root.find("asset"),"mesh",name=cname,vertex=numbers(vertices))
            sub(world,"geom",name=cname,type="mesh",mesh=cname,contype="2",conaffinity="1",priority="0",condim=cc["condim"],
                friction=numbers([cc["static_sliding_friction"],cc["torsional_friction"],cc["rolling_friction"]]),
                solref=numbers(cc["solref"]),solimp=numbers(cc["solimp"]))
            mappings.append({"name":cname,"kind":"original_closed_convex_component","map_mesh":item["id"],
                "source_geometry_sha256":item["convex_sha256"]})
    p=shell["perimeter"]
    bounds=p.get("target_bounds_m",shell["bounds_m"])
    lo,hi=np.asarray(bounds["lo"]),np.asarray(bounds["hi"])
    t=float(p["collision_thickness_m"])
    clearance=float(p["outside_clearance_m"])
    bottom,top=float(p["collision_bottom_m"]),float(p["collision_top_m"])
    walls=[
        ("south",[(lo[0]+hi[0])/2,lo[1]-clearance-t/2,(top+bottom)/2],[(hi[0]-lo[0])/2+clearance+t,t/2,(top-bottom)/2]),
        ("north",[(lo[0]+hi[0])/2,hi[1]+clearance+t/2,(top+bottom)/2],[(hi[0]-lo[0])/2+clearance+t,t/2,(top-bottom)/2]),
        ("west",[lo[0]-clearance-t/2,(lo[1]+hi[1])/2,(top+bottom)/2],[t/2,(hi[1]-lo[1])/2+clearance+t,(top-bottom)/2]),
        ("east",[hi[0]+clearance+t/2,(lo[1]+hi[1])/2,(top+bottom)/2],[t/2,(hi[1]-lo[1])/2+clearance+t,(top-bottom)/2])]
    for name,pos,size in walls:
        sub(world,"geom",name="arena_"+name,type="box",pos=numbers(pos),size=numbers(size),
            contype="2",conaffinity="1",priority="0",condim=cc["condim"],
            friction=numbers([cc["static_sliding_friction"],cc["torsional_friction"],cc["rolling_friction"]]),
            solref=numbers(cc["solref"]),solimp=numbers(cc["solimp"]))
        mappings.append({"name":"arena_"+name,"kind":"boundary_box","position_m":pos,"half_size_m":size})
    return mappings

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--repo",type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--geometry-directory",type=Path)
    parser.add_argument("--spawn",default="default")
    parser.add_argument("--clearance",type=float,default=.03)
    parser.add_argument("--vehicle-only",action="store_true")
    a=parser.parse_args()
    if mujoco.__version__!="4.0.1" or mujoco.mj_version()!=4000001:
        raise RuntimeError("Pinned MuJoCo 4.0.1 is required")
    repo=a.repo.resolve();output=a.output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    rig_path=repo/"assets/vehicles/aim_sentry/ue_mesh_rig.json"
    profile_path=repo/"assets/vehicles/aim_sentry/drive-profile.json"
    config_path=repo/"assets/vehicles/aim_sentry/mujoco/backend.json"
    map_path=repo/"assets/maps/rmuc2027/map.json"
    shell_path=repo/"assets/maps/rmuc2027/arena-shell.json"
    rig,profile,config,map_spec,shell=map(load_json,(rig_path,profile_path,config_path,map_path,shell_path))
    if rig["id"]!="aim_sentry" or config["mujoco_version"]!=mujoco.__version__:
        raise ValueError("Unsupported vehicle/backend identity")
    physical_map(repo)
    report={"schema":"rmsim_mujoco_scene_v1","status":"generating","vehicle_id":rig["id"],
        "created_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model_xml":"model.xml","rig_file":relative(rig_path,repo),"drive_profile_file":relative(profile_path,repo),
        "gimbal_control":config["gimbal_control"],"mujoco_version":mujoco.__version__,
        "contact_mapping":dict(config["contact_mapping"],effective_wheel_sliding=source_average_friction(profile,config,True),
            effective_chassis_sliding=source_average_friction(profile,config,False)),
        "timestep_s":config["timestep_s"],"coordinate_system":"ROS_RIGHT_HANDED_X_FORWARD_Y_LEFT_Z_UP",
        "gravity_mps2":config["gravity_mps2"],"gravity_source":config["gravity_source"],"upper_mass_policy":config["upper_mass_policy"],
        "units":"SI","calibrated":False,"diagnostic_plane_only":bool(a.vehicle_only),
        "source_sha256":{relative(p,repo):digest(p) for p in (rig_path,profile_path,config_path,map_path,shell_path,Path(__file__),Path(__file__).with_name("geometry.py"),Path(__file__).with_name("model_contract.py"))}}
    report_path=output/"manifest.json"
    def save(): report_path.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    save()
    try:
        hulls,provenance=find_collision_records(repo,rig)
        inertias=compute_inertias(rig,profile,config,hulls)
        report["vehicle_collision"]=provenance
        report["inertias"]=inertias
        if a.vehicle_only:
            groups=[];geometry_report=None
            spawn={"id":"vehicle_only","root_position_m":[0,0,.03],"root_quaternion_wxyz":[1,0,0,0]}
        else:
            geometry_dir=a.geometry_directory or output/"raw_geometry"
            if a.geometry_directory is None:extract_rmuc(repo,geometry_dir)
            geometry_report,groups=load_geometry(geometry_dir,repo)
            spawn=spawn_for(map_spec,rig,groups,a.spawn,a.clearance)
            report["geometry_manifest"]=str((geometry_dir/"geometry.json").resolve())
            report["geometry_manifest_sha256"]=digest(geometry_dir/"geometry.json")
        root=vehicle_xml(rig,profile,config,hulls,spawn,inertias)
        if a.vehicle_only:
            cc=config["contact"]
            sub(root.find("worldbody"),"geom",name="diagnostic_plane",type="plane",size="0 0 0.1",
                contype="2",conaffinity="1",priority="0",condim=cc["condim"],friction=numbers([cc["static_sliding_friction"],cc["torsional_friction"],cc["rolling_friction"]]),
                solref=numbers(cc["solref"]),solimp=numbers(cc["solimp"]))
            mappings=[{"name":"diagnostic_plane","kind":"diagnostic_only"}]
        else:mappings=add_static(root,groups,shell,config)
        xml_path=output/"model.xml"
        ET.ElementTree(root).write(xml_path,encoding="utf-8",xml_declaration=True)
        start=time.perf_counter()
        model=mujoco.MjModel.from_xml_path(str(xml_path))
        data=mujoco.MjData(model);mujoco.mj_forward(model,data)
        report["compile_seconds"]=time.perf_counter()-start
        if any(int(x.number) for x in data.warning):
            raise ValueError("MuJoCo reported initialization warnings")
        names=[x["name"] for x in rig["parts"]]
        report["body_names"]=names
        report["joints"]={name:"chassis_free" if name=="body" else "j_"+name for name in names}
        report["actuators"]={name:("a_spring_" if name.startswith("suspension_") else "a_")+name
            for name in names if name!="body"}
        report["passive_springs"]=[report["actuators"][x["part"]] for x in rig["suspension"]]
        report["wheels"]=[{k:v for k,v in w.items() if k!="collision_vertices_m"} for w in rig["wheels"]]
        report["static_objects"]=mappings
        report["map_identity"]={"map_id":map_spec["id"],"map_sha256":digest(map_path),
            "boundary_sha256":digest(shell_path),"physics_contract":shell["perimeter"]["physics_contract"],
            "diagnostic_plane_only":bool(a.vehicle_only)}
        report["physics_identity"]=physics_identity(repo)
        report["physics_identity_sha256"]=identity_digest(report["physics_identity"])
        report["runtime_bindings"]=runtime_bindings(repo,output,rig_path,profile_path,config_path)
        report["spawn"]=spawn
        report["initial_qpos"]=data.qpos.tolist()
        report["model_counts"]={n:int(getattr(model,n)) for n in
            ("nq","nv","nbody","njnt","nu","ngeom","nmesh","nflex","nflexvert","nflexelem")}
        report["total_mass_kg"]=float(model.body_mass.sum())
        report["model_sha256"]=digest(xml_path)
        report["geometry_summary"]=None if geometry_report is None else {
            "vertices":geometry_report["total_vertices"],"triangles":geometry_report["total_triangles"],
            "method":geometry_report["method"]}
        report["limitations"]=["Inertias are derived from homogeneous source convex geometry and configured masses, not measured hardware inertias.",
            "MuJoCo friction/contact solver semantics require their own native acceptance.",
            "Fixed terrain flex uses the explicitly recorded finite collision shell radius.",
            "Original UE visual assets and source materials are not regenerated.",
            "A successful compile is not a movement, real-time, sensor or navigation acceptance."]
        report["status"]="compiled_pending_runtime_acceptance"
        save()
        print(json.dumps({"status":report["status"],"manifest":str(report_path),
            "counts":report["model_counts"],"mass_kg":report["total_mass_kg"],"compile_seconds":report["compile_seconds"]}),flush=True)
    except Exception as error:
        report.update(status="failed",error=repr(error));save();raise

if __name__=="__main__":main()
