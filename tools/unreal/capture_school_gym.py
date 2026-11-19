"""Capture school gym from whole hall, field, and window line-of-sight; enforce native facts."""
from pathlib import Path
import argparse,json,subprocess,hashlib,time,datetime
from PIL import Image
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 a=argparse.ArgumentParser()
 a.add_argument('--root',type=Path,default=Path('D:/simulator'))
 a.add_argument('--engine',type=Path,default=Path('E:/simulator-tools/UE_5.8'))
 a.add_argument('--output',type=Path,required=True)
 a.add_argument('--views',nargs='+',default=['CompleteGym','DriverView','WindowView'])
 a.add_argument('--capture-delay',type=float,default=40)
 a.add_argument('--timeout',type=float,default=900)
 args=a.parse_args();root=args.root.resolve();out=args.output.resolve()
 if out.exists():raise FileExistsError('Output path already used')
 out.mkdir(parents=True)
 project=root/'unreal/RMSim/RMSim.uproject'
 ue=args.engine/'Engine/Binaries/Win64/UnrealEditor.exe'
 cameras={
 'CompleteGym':{'position':[0,-16.2,6.6],'target':[0,0,1.8],'fov':84},
 'DriverView':{'position':[-8,-6,1.15],'target':[8,3,2],'fov':78},
 'WindowView':{'position':[0,12,7.7],'target':[0,36,6.1],'fov':74},
 'CenterPanorama':{'position':[-8,-6,6.5],'target':[8,7,1.5],'fov':84},
 'OppositeView':{'position':[19,14,7.9],'target':[0,-4,3],'fov':75},
 'FieldWindow':{'position':[-5,0,1.25],'target':[-5,28,6.0],'fov':78},
 'FieldEndWindow':{'position':[-8,-4,1.3],'target':[-35,0,6.0],'fov':75},
 'BoundaryClose':{'position':[8,6,1.6],'target':[8,8.001566,0.0],'fov':65},
 'BoundaryCorner':{'position':[11,5.5,2.0],'target':[14,8,0.2],'fov':75},
 'BoundaryNorth':{'position':[0,6.0,.65],'target':[7,8.001566,-.18],'fov':70},
 'BoundarySouth':{'position':[0,-6.0,.65],'target':[-7,-8.001566,-.18],'fov':70},
 'FieldSky':{'position':[-5,-5,1.25],'target':[-5,22,9.5],'fov':78}
 }
 def ros(v):return ','.join(f'{x:.7g}' for x in (v[0],-v[1],v[2]))
 config=root/'assets/maps/rmuc2027/arena-shell.json'
 layout=root/'assets/maps/rmuc2027/arena/sketchupbox-school-gym/layout.json'
 report={'status':'running','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
         'manifest_sha256':sha(config),'layout_sha256':sha(layout),
         'source_map_sha256':sha(root/'assets/maps/rmuc2027/map.json'),
         'vehicle_sha256':sha(root/'assets/vehicles/aim_sentry/ue_mesh_rig.json'),
         'ue_module_sha256':sha(root/'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll'),'views':[]}
 try:
  for name in args.views:
   view=cameras[name];folder=out/name;folder.mkdir()
   cmd=[str(ue),str(project),'/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode',
      '-game','-ContactPhysics','-RenderOffScreen','-unattended','-NoSplash','-NoSound','-CustomConfig=RMSimGymLumen','-d3d12','-sm6',
      '-ini:Engine:[DevOptions.Shaders]:bAllowCompilingThroughWorkers=True,[DevOptions.Shaders]:NumUnusedShaderCompilingThreads=20,[DevOptions.Shaders]:NumUnusedShaderCompilingThreadsDuringGame=20,[DevOptions.Shaders]:ShaderCompilerCoreCountThreshold=64',
      '-MapFile='+str(root/'assets/maps/rmuc2027/map.json'),
      '-MeshRigFile='+str(root/'assets/vehicles/aim_sentry/ue_mesh_rig.json'),
      '-GymCameraPosition='+ros(view['position']),'-GymCameraTarget='+ros(view['target']),'-GymCameraFov='+str(view['fov']),
      '-GymProbeOutput='+str(folder/'boundary.json'),'-CaptureDir='+str(folder),
      '-CaptureDelay='+str(args.capture_delay),'-ExitAfterCapture',
      '-ResX=1600','-ResY=1000','-ForceRes','-abslog='+str(folder/'ue.log')]
   (folder/'command.json').write_text(json.dumps(cmd,indent=2),encoding='utf-8')
   print('SCHOOL_RUNTIME_VIEW_START',name,flush=True)
   start=time.monotonic()
   with (folder/'process.log').open('w',encoding='utf-8') as log:
    p=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,cwd=root)
    (folder/'pid.txt').write_text(str(p.pid))
    try:code=p.wait(args.timeout)
    except subprocess.TimeoutExpired:
     p.terminate();p.wait(20);raise RuntimeError('Capture timeout '+name)
   if code:raise RuntimeError('UE capture exited '+str(code)+' for '+name)
   probe=json.loads((folder/'boundary.json').read_text(encoding='utf-8-sig'))
   for check in (
    probe['status']=='passed',probe['sweep_samples']==96,probe['sweep_passed']==96,
    probe['all_boundary_parts_outside_source_bounds'] is True,
    probe['physics_contract']=='outside_source_bounds_v1',
    abs(probe['outside_clearance_m']-.001)<1e-6,
    probe['native_mesh_assets_loaded']==23,probe['native_mesh_instances']==57,
    probe['gi_method_requested']=='lumen',probe['reflection_method_requested']=='lumen',
    probe['procedural_parquet_enabled'] is False,probe['fixed_wall_branding_enabled'] is False):
    if not check:raise RuntimeError('School native field or Lumen QA failed: '+name)
   images=[]
   for fname in ('overview.png','aim_camera.png'):
    p=folder/fname
    with Image.open(p) as im:
     im.load()
     if min(im.size)<200:raise ValueError('Captured resolution too small')
     size=im.size
    images.append({'file':str(p),'size':size,'sha256':sha(p)})
   logtext=(folder/'ue.log').read_text(encoding='utf-8-sig',errors='replace')
   if 'RMSIM_GYM_CAMERA_CUSTOM' not in logtext or 'RMSIM_GYM_CAMERA_INVALID' in logtext or 'PCD3D_SM6' not in logtext:
    raise RuntimeError('Custom camera or SM6 not validated')
   report['views'].append({'name':name,'status':'native_capture_passed_pending_visual_inspection',
                           'seconds':round(time.monotonic()-start,2),'images':images,
                           'probe':str(folder/'boundary.json')})
   (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
   print('SCHOOL_RUNTIME_VIEW_PASSED',name,'seconds',round(time.monotonic()-start,1),flush=True)
  report['status']='native_views_passed_pending_human_visual_review'
 except Exception as e:
  report.update(status='failed',error=str(e));raise
 finally:
  (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
 print('SCHOOL_RUNTIME_CAPTURE_COMPLETE',len(report['views']),flush=True)
if __name__=='__main__':main()
