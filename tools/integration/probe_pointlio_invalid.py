"""Regression for an invalid parameter type formerly swallowed by the original node."""
import json,os,subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[2]
out=root/'artifacts/sensors/lidar/pointlio-invalid-type'
out.mkdir(parents=True,exist_ok=False)
config=out/'wrong-type.yaml'
config.write_text('/**:\n  ros__parameters:\n    mapping.extrinsic_R: [1,0,0,0,1,0,0,0,1]\n',encoding='utf-8')
prefix=root/'ros2_ws/install_lio/point_lio'
command=[str(prefix/'lib/point_lio/pointlio_mapping.exe'),'--ros-args','--params-file',str(prefix/'share/point_lio/config/mid360.yaml'),'--params-file',str(config)]
result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
(out/'runtime.log').write_text(result.stdout+result.stderr,encoding='utf-8')
passed=result.returncode==2 and 'invalid type' in result.stderr and 'IMU Initializing' not in result.stderr
(out/'verification.json').write_text(json.dumps(dict(passed=passed,process_exit=result.returncode,scope='wrong-type configuration exits before estimator initialization'),indent=2)+'\n',encoding='utf-8')
assert passed,(result.returncode,result.stderr)
print('Original Point-LIO invalid parameter fails fast with exit2')
