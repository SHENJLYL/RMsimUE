"""Run one owned full-editor school asset task and retain exact process evidence."""
from pathlib import Path
import argparse,json,subprocess,hashlib,time,datetime
r=Path('D:/simulator')
a=argparse.ArgumentParser();a.add_argument('--out',type=Path,required=True);a.add_argument('--script',type=Path,required=True);a.add_argument('--extra',nargs='*',default=[]);args=a.parse_args()
o=args.out.resolve();o.mkdir(parents=True,exist_ok=False)
check=subprocess.run(['powershell','-NoProfile','-Command',"$p=@(Get-CimInstance Win32_Process -Filter \"Name LIKE 'Unreal%'\" | Where-Object {$_.CommandLine -like '*RMSim.uproject*'}); $p.Count"],capture_output=True,text=True)
if check.returncode or check.stdout.strip()!='0':raise RuntimeError('An RMSim Editor/game is already running: '+check.stdout)
cmd=['E:/simulator-tools/UE_5.8/Engine/Binaries/Win64/UnrealEditor-Cmd.exe',str(r/'unreal/RMSim/RMSim.uproject'),'/Engine/Maps/Entry','-ExecutePythonScript='+str(args.script.resolve()),'-VenueAssetRoot='+str(r/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/ue-import-05-fidelity'),'-VenueImportReport='+str(o/'report.json'),'-CustomConfig=RMSimGymLumen','-d3d12','-sm6','-RenderOffScreen','-unattended','-NoSplash','-NoSound','-nop4','-abslog='+str(o/'ue.log'),*args.extra]
(o/'command.json').write_text(json.dumps(cmd,indent=2),encoding='utf-8')
t=time.monotonic();record={'status':'running','started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'command':cmd,'script_sha256':hashlib.sha256(args.script.read_bytes()).hexdigest()}
with (o/'process.log').open('w',encoding='utf-8') as log:
 p=subprocess.Popen(cmd,cwd=r,stdout=log,stderr=subprocess.STDOUT)
 record['pid']=p.pid;(o/'execution.json').write_text(json.dumps(record,indent=2),encoding='utf-8');print('SCHOOL_EDITOR_PID',p.pid,flush=True)
 try:rc=p.wait(timeout=1800)
 except subprocess.TimeoutExpired:
  p.terminate();rc=p.wait(timeout=30);record['timeout']=True
record.update(status='exited',exit_code=rc,seconds=time.monotonic()-t)
(o/'execution.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
print('SCHOOL_EDITOR_EXIT',rc,'SECONDS',round(record['seconds'],2),flush=True)
