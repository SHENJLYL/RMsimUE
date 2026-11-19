"""Observe an already running interactive school window; keep it open for the user."""
from pathlib import Path
import argparse,ctypes,ctypes.wintypes as wt,hashlib,importlib.util,json,time,datetime
R=Path(__file__).resolve().parents[2]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 a=argparse.ArgumentParser();a.add_argument('--pid',type=int,required=True);a.add_argument('--out',type=Path,required=True);a.add_argument('--duration',type=float,default=150);x=a.parse_args()
 x.out.mkdir(parents=True,exist_ok=False)
 s=importlib.util.spec_from_file_location('school_window',R/'tools/unreal/probe_school_gpu_visible.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
 k=ctypes.windll.kernel32;k.OpenProcess.restype=wt.HANDLE;k.GetExitCodeProcess.argtypes=[wt.HANDLE,ctypes.POINTER(wt.DWORD)];k.CloseHandle.argtypes=[wt.HANDLE]
 h=k.OpenProcess(0x1000,False,x.pid)
 if not h:raise RuntimeError('Cannot inspect requested game process')
 log=R/'unreal/RMSim/Saved/Logs/RMSim.log'
 report={'status':'running','pid':x.pid,'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'requested_seconds':x.duration,'observations':[],
  'manifest_sha256':sha(R/'assets/maps/rmuc2027/arena-shell.json'),'layout_sha256':sha(R/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/layout.json'),'module_sha256':sha(R/'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll')}
 start=time.monotonic()
 try:
  while True:
   ec=wt.DWORD();ok=k.GetExitCodeProcess(h,ctypes.byref(ec));elapsed=time.monotonic()-start
   visible=m.window_for_pid(x.pid)
   entry={'elapsed_s':round(elapsed,2),'running':bool(ok and ec.value==259),'visible_window':bool(visible),'client_sizes':[[v[1],v[2]] for v in visible]}
   report['observations'].append(entry);(x.out/'report.json').write_text(json.dumps(report,indent=2))
   if not entry['running']:raise RuntimeError('Interactive window exited during observation: '+str(ec.value))
   if elapsed>=x.duration:break
   time.sleep(min(10,x.duration-elapsed))
  text=log.read_text(encoding='utf-8-sig',errors='replace')
  terms=['GPU timeout','GPU Crashed','TerminateOnGPUCrash','Writing Aftermath dump','Non-Nanite Marking Job Queue overflow','Virtual Shadow Map Page Pool overflow']
  warnings={t:text.count(t) for t in terms}
  required=['PCD3D_SM6','r.Shadow.Virtual.Enable:0','RMSIM_CAMPUS_GYM_READY layout=57','native_assets=23 native_instances=57','IMPORT_LAB_READY','M_SchoolLandscape.M_SchoolLandscape']
  checks={t:t in text for t in required}
  assert any(v['visible_window'] for v in report['observations'])
  assert all(checks.values()) and not any(warnings.values())
  report.update(status='passed_kept_open',seconds=round(time.monotonic()-start,2),warnings=warnings,checks=checks,left_running=True)
 except Exception as e:
  report.update(status='failed',error=str(e));raise
 finally:
  k.CloseHandle(h);(x.out/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({k:report[k] for k in ['status','pid','seconds','left_running']}),flush=True)
if __name__=='__main__':main()
