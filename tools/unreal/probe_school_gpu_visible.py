"""Run a visible 1600x1000 SM6 gym game for sustained GPU-stability QA.

Requires no foreground capture and doesn't alter input, physics or rendering config.
Stores exact command, hashes, process state, key graphics warnings, and VRAM usage.
Closes only its own visible UE window after the test interval.
"""
from pathlib import Path
import ctypes
import ctypes.wintypes as wt
import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import time

def hash_file(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda:f.read(1048576),b""):
            h.update(part)
    return h.hexdigest()

def window_for_pid(pid):
    u=ctypes.windll.user32
    found=[]
    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd,param):
        target=wt.DWORD()
        u.GetWindowThreadProcessId(hwnd,ctypes.byref(target))
        if target.value==pid and u.IsWindowVisible(hwnd):
            rc=wt.RECT()
            u.GetClientRect(hwnd,ctypes.byref(rc))
            if rc.right>300 and rc.bottom>200:
                found.append((hwnd,rc.right,rc.bottom))
        return True
    u.EnumWindows(cb,0)
    return found

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,default=Path("D:/simulator"))
    ap.add_argument("--engine",type=Path,default=Path("E:/simulator-tools/UE_5.8"))
    ap.add_argument("--out",type=Path,required=True)
    ap.add_argument("--duration",type=float,default=150)
    args=ap.parse_args()
    root=args.root.resolve();out=args.out.resolve()
    if out.exists():raise FileExistsError("Evidence directory already exists: "+str(out))
    out.mkdir(parents=True)
    project=root/"unreal/RMSim/RMSim.uproject"
    editor=args.engine/"Engine/Binaries/Win64/UnrealEditor.exe"
    config=root/"unreal/RMSim/Config/Custom/RMSimGymLumen/DefaultEngine.ini"
    layout=root/"assets/maps/rmuc2027/arena/sketchupbox-school-gym/layout.json"
    cmd=[str(editor),str(project),"/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode",
         "-game","-ResX=1600","-ResY=1000","-NoSplash","-NoSound",
         "-MeshRigFile="+str(root/"assets/vehicles/aim_sentry/ue_mesh_rig.json"),
         "-GimbalYaw=0","-GimbalPitch=0","-windowed",
         "-MapFile="+str(root/"assets/maps/rmuc2027/map.json"),"-SpawnId=default",
         "-CustomConfig=RMSimGymLumen","-d3d12","-sm6","-ContactPhysics",
         "-abslog="+str(out/"gpu.log")]
    report={"status":"running","started_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
            "duration_target_s":args.duration,"config_sha256":hash_file(config),
            "layout_sha256":hash_file(layout),"field_map_sha256":hash_file(root/"assets/maps/rmuc2027/map.json"),
            "rig_sha256":hash_file(root/"assets/vehicles/aim_sentry/ue_mesh_rig.json"),
            "rmsim_module_sha256":hash_file(root/"unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll"),
            "command":cmd,"observations":[]}
    (out/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    def gpu_info():
        try:
            return subprocess.run(["nvidia-smi","--query-gpu=utilization.gpu,memory.used,temperature.gpu,power.draw",
                                  "--format=csv,noheader,nounits"],capture_output=True,text=True,timeout=8).stdout.strip()
        except Exception as e:return "nvidia-smi-error="+str(e)
    start=time.monotonic()
    with (out/"process.log").open("w",encoding="utf-8") as logfile:
        p=subprocess.Popen(cmd,cwd=root,stdout=logfile,stderr=subprocess.STDOUT)
        report["pid"]=p.pid
        print("VISIBLE_GPU_PROBE_LAUNCHED",p.pid,flush=True)
        try:
            max_time=args.duration+45
            window_seen=False
            while time.monotonic()-start<max_time:
                alive=p.poll() is None
                views=window_for_pid(p.pid)
                if views:window_seen=True
                elapsed=round(time.monotonic()-start,1)
                if int(elapsed)%10 in (0,1):
                    entry={"elapsed_s":elapsed,"running":alive,"visible_window":bool(views),
                           "client_sizes":[[x[1],x[2]] for x in views],"gpu":gpu_info()}
                    report["observations"].append(entry)
                    print("VISIBLE_GPU_PROBE_SAMPLE",json.dumps(entry),flush=True)
                    (out/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
                if not alive:
                    raise RuntimeError("Visible UE game crashed or exited before hold period; exit="+str(p.returncode))
                if elapsed>=args.duration:
                    if not window_seen:
                        raise RuntimeError("Visible window never appeared")
                    break
                time.sleep(3)
            else:
                raise RuntimeError("Visible game hold timed out")
            # Graceful WM_CLOSE to our own window only.
            hwnds=window_for_pid(p.pid)
            if not hwnds:raise RuntimeError("Game window disappeared at hold completion")
            for hwnd,_,_ in hwnds:ctypes.windll.user32.PostMessageW(hwnd,0x0010,0,0)
            try:exit_code=p.wait(timeout=50)
            except subprocess.TimeoutExpired:
                report["status"]="hung_at_graceful_exit"
                raise
            if exit_code!=0:raise RuntimeError("Visible UE closed with nonzero exit="+str(exit_code))
            log=(out/"gpu.log").read_text(encoding="utf-8-sig",errors="replace")
            crash_terms=("GPU timeout","TerminateOnGPUCrash","GPU Crashed","Writing Aftermath dump","Non-Nanite Marking Job Queue overflow",
                         "Virtual Shadow Map Page Pool overflow")
            warnings={term:log.count(term) for term in crash_terms}
            must=("r.Shadow.Virtual.Enable:0","gi_requested=lumen","reflection_requested=lumen",
                  "RMSIM_CAMPUS_GYM_READY","IMPORT_LAB_READY","PCD3D_SM6")
            checks={term:(term in log) for term in must}
            report.update(status="passed" if all(checks.values()) and not any(warnings.values()) else "failed",
                           seconds=round(time.monotonic()-start,2),exit_code=exit_code,
                           warnings=warnings,checks=checks,visible_window_seen=window_seen,
                           ended_utc=dt.datetime.now(dt.timezone.utc).isoformat())
            print("VISIBLE_GPU_PROBE_RESULT",json.dumps({k:report[k] for k in ("status","seconds","warnings","checks","exit_code")}),flush=True)
        except Exception as error:
            report.update(status="failed",error=str(error),seconds=round(time.monotonic()-start,2))
            raise
        finally:
            (out/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
if __name__=="__main__":main()
