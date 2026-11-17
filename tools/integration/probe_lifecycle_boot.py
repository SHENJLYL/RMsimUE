"""Isolate packaged lifecycle-manager startup from UE, Point-LIO and navigation input."""
import argparse,json,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args()
root=Path(__file__).resolve().parents[2];args.output.mkdir(parents=True,exist_ok=False)
config=args.output/'parameters.yaml'
config.write_text(json.dumps({'/**':{'ros__parameters':{'node_names':['no_such_server'],'autostart':False,'use_sim_time':False}}}),encoding='utf-8')
exe=root/'artifacts/runtime/navigation/windows/lifecycle_manager.exe'
with (args.output/'runtime.log').open('w',encoding='utf-8') as log:
    child=subprocess.Popen([str(exe),'--ros-args','--params-file',str(config)],stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
    try:code=child.wait(timeout=6);alive=False
    except subprocess.TimeoutExpired:alive=True;code=None;child.terminate();child.wait(timeout=10)
report=dict(alive_after_six_seconds=alive,early_exit=code,scope='isolated original lifecycle manager, no UE/sensors/autostart')
(args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))
