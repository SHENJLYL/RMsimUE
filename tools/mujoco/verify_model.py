"""Verify an AIM/RMUC package before native launch; optional explicit diagnostics."""
from __future__ import annotations
import argparse
import datetime
import json
from pathlib import Path
import sys
import time
from model_contract import digest,load_json,verify_manifest

def compile_check(manifest,model_path):
    import mujoco
    if mujoco.__version__!="4.0.1" or mujoco.mj_version()!=4000001:
        raise ValueError("Pinned MuJoCo 4.0.1 Python runtime is required")
    start=time.perf_counter()
    model=mujoco.MjModel.from_xml_path(str(model_path));data=mujoco.MjData(model)
    mujoco.mj_forward(model,data)
    counts={key:int(getattr(model,key)) for key in manifest["model_counts"]}
    if counts!=manifest["model_counts"]:raise ValueError("Compiled model counts differ from manifest")
    warnings=[int(w.number) for w in data.warning]
    if any(warnings):raise ValueError("MuJoCo initialization warning")
    if abs(model.opt.timestep-manifest["timestep_s"])>1e-12:
        raise ValueError("Compiled timestep changed")
    if abs(float(model.body_mass.sum())-manifest["total_mass_kg"])>1e-9:
        raise ValueError("Compiled vehicle mass changed")
    return model,data,{"status":"passed","scope":"MuJoCo 4.0.1 XML compilation and initialization",
        "elapsed_seconds":time.perf_counter()-start,"version_integer":mujoco.mj_version(),
        "counts":counts,"total_mass_kg":float(model.body_mass.sum()),"warnings":warnings}

def diagnostic_motion(manifest,model,data,repo):
    import mujoco
    import numpy as np
    if not manifest["diagnostic_plane_only"] or model.nflex:
        raise ValueError("The flat-plane motion diagnostic requires an explicit diagnostic model")
    profile=load_json(repo/manifest["drive_profile_file"])
    damping=float(profile["physics"]["MotorDampingNmS"])
    ids=[mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_ACTUATOR,"a_"+w["part"]) for w in manifest["wheels"]]
    if any(i<0 for i in ids):raise ValueError("Missing wheel actuator")
    start=time.perf_counter();snapshots={};max_contacts=0
    for step in range(5000):
        t=step*model.opt.timestep
        speed=.5*min(1.0,(t-.8)/.5) if .8<=t<3.8 else 0.0
        for index,wheel in zip(ids,manifest["wheels"]):
            data.ctrl[index]=damping*speed/wheel["radius_m"]
        mujoco.mj_step(model,data)
        max_contacts=max(max_contacts,int(data.ncon))
        if step+1 in (800,3800,5000):
            snapshots[str(step+1)]={"time_s":float(data.time),"root_position_m":data.qpos[:3].tolist(),
                "root_velocity_m_s":data.qvel[:3].tolist(),"contacts":int(data.ncon)}
    warnings=[int(w.number) for w in data.warning]
    drive=snapshots["3800"];stop=snapshots["5000"]
    passed=(np.isfinite(data.qpos).all() and np.isfinite(data.qvel).all()
        and not any(warnings) and abs(drive["root_velocity_m_s"][0]-.5)<.03
        and abs(stop["root_velocity_m_s"][0])<.01
        and drive["root_position_m"][0]>1.2 and abs(stop["root_position_m"][1])<.03
        and max_contacts>0)
    result={"status":"passed" if passed else "failed",
        "scope":"Original AIM hulls and joints on an explicit flat diagnostic plane; no UE, full-field or sensor acceptance",
        "steps":5000,"simulated_seconds":float(data.time),"wall_seconds":time.perf_counter()-start,
        "command":{"settle_s":.8,"forward_m_s":.5,"ramp_s":.5,"drive_end_s":3.8,"stop_end_s":5.0},
        "pose_overrides":0,"warnings":warnings,"maximum_contacts":max_contacts,"samples":snapshots}
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo",type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument("--manifest",type=Path,required=True)
    p.add_argument("--map-file",type=Path)
    p.add_argument("--output",type=Path,help="Fresh evidence directory; otherwise print JSON only")
    p.add_argument("--allow-diagnostic",action="store_true")
    p.add_argument("--preflight-only",action="store_true")
    p.add_argument("--compile",action="store_true")
    p.add_argument("--diagnostic-motion",action="store_true")
    a=p.parse_args()
    if a.preflight_only and (a.compile or a.diagnostic_motion):
        p.error("--preflight-only cannot be combined with compilation or motion")
    if a.output:a.output.mkdir(parents=True,exist_ok=False)
    report={"schema":"rmsim_mujoco_verification_v1","status":"running",
        "created_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "command":sys.argv,"python":sys.executable,"verifier_sha256":digest(Path(__file__))}
    exit_code=0
    try:
        manifest=load_json(a.manifest)
        report["preflight"]=verify_manifest(a.manifest,a.repo,a.allow_diagnostic,a.map_file)
        if a.compile or a.diagnostic_motion:
            model,data,report["compilation"]=compile_check(manifest,Path(report["preflight"]["model_xml"]))
            if a.diagnostic_motion:
                report["diagnostic_motion"]=diagnostic_motion(manifest,model,data,a.repo.resolve())
                if report["diagnostic_motion"]["status"]!="passed":raise ValueError("Flat-plane motion acceptance failed")
        report["status"]="passed"
    except Exception as error:
        report.update(status="failed",error=repr(error));exit_code=1
    rendered=json.dumps(report,indent=2,ensure_ascii=False)+"\n"
    if a.output:(a.output/"report.json").write_text(rendered,encoding="utf-8")
    print(rendered,flush=True)
    raise SystemExit(exit_code)

if __name__=="__main__":main()
