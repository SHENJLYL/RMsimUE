param(
 [Parameter(Mandatory=$true)][string]$EngineRoot,
 [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
 [string]$ModelManifest='',
 [int]$Port=19673,
 [int]$ProbeTimeoutSeconds=180
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
. (Join-Path $root 'tools/unreal/Common.ps1')
$ue=Get-UnrealTools -EngineRoot $EngineRoot
$out=[IO.Path]::GetFullPath($EvidenceDirectory)
if(Test-Path -LiteralPath $out){throw 'Use a fresh shared-drive evidence directory.'}
New-Item -ItemType Directory -Path $out | Out-Null
if(-not $ModelManifest){$ModelManifest=Join-Path $root 'assets/vehicles/aim_sentry/mujoco/rmuc2027/manifest.json'}
$python=Join-Path $root 'artifacts/environments/mujoco-4.0.1/Scripts/python.exe'
$project=$ue.Project.Replace('\','/').ToLowerInvariant()
$sha=[Security.Cryptography.SHA256]::Create()
try{$key=([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($project)))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
$mutex=[Threading.Mutex]::new($false,'Local\RMSimSceneImport_'+$key)
$locked=$false;$process=$null;$child=$null
$report=[ordered]@{schema='rmsim_shared_drive_profile_gate_v1';status='started';started_utc=[DateTime]::UtcNow.ToString('o');
 scope='native Windows complete RMUC MuJoCo model; shared vehicle controller and watchdog; no sensor, navigation or realtime acceptance';
 coordinates='ROS ENU, base_link x forward/y left and positive yaw left';maximum_planar_excursion_m=1.0}
function Save-Report {$report | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath (Join-Path $out 'verification.json') -Encoding UTF8}
function Run-OwnedProbe([string]$Executable,[string[]]$Arguments,[string]$Name) {
 $script:child=Start-Process -FilePath $Executable -ArgumentList @($Arguments|ForEach-Object{Quote-UnrealArgument ([string]$_)}) -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $out ($Name+'.stdout.log')) -RedirectStandardError (Join-Path $out ($Name+'.stderr.log'))
 [void]$script:child.Handle
 if(-not $script:child.WaitForExit($ProbeTimeoutSeconds*1000)){
  & taskkill.exe /PID $script:child.Id /T /F | Out-Null
  throw ($Name+' timed out; owned child tree stopped and evidence retained.')
 }
 $script:child.WaitForExit();$script:child.Refresh();$exit=$script:child.ExitCode
 $script:child.Dispose();$script:child=$null
 if($null -eq $exit -or $exit -ne 0){throw ($Name+' failed with exit '+$exit)}
}
try {
 try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
 if(-not $locked){throw 'RMSim shared build/import/launch lock is held.'}
 $active=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match 'UnrealEditor|UnrealBuildTool|dotnet' -and $_.CommandLine -and $_.CommandLine.Replace('\','/').ToLowerInvariant().Contains($project)})
 if($active.Count){throw ('RMSim is in use by PID '+(($active|ForEach-Object ProcessId)-join ','))}
 $manifest=(Resolve-Path -LiteralPath $ModelManifest).Path
 $model=Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
 if($model.diagnostic_plane_only -or $model.map_identity.map_id -ne 'rmuc2027'){throw 'This gate requires the complete original RMUC model.'}
 $map=Join-Path $root 'assets/maps/rmuc2027/map.json'
 $arena=Join-Path $root 'assets/maps/rmuc2027/arena-shell.json'
 $rig=Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json'
 $profile=Join-Path $root 'assets/vehicles/aim_sentry/drive-profile.json'
 $inputs=[ordered]@{
  model_manifest=$manifest;model_xml=(Join-Path (Split-Path -Parent $manifest) $model.model_xml);
  module=(Join-Path $root 'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll');
  runtime=(Join-Path $root 'unreal/RMSim/Binaries/Win64/mujoco.dll');
  map=$map;arena_shell=$arena;rig=$rig;drive_profile=$profile;
  gate=$PSCommandPath;tcp_probe=(Join-Path $PSScriptRoot 'probe_shared_drive_profile.py');
  protocol=(Join-Path $root 'ros2_ws/src/rmsim_bridge/rmsim_bridge/protocol.py');
  drive_source=(Join-Path $root 'unreal/RMSim/Source/RMSim/VehicleDrive.cpp');
  drive_header=(Join-Path $root 'unreal/RMSim/Source/RMSim/VehicleRig.h');
  native_vehicle_source=(Join-Path $root 'unreal/RMSim/Source/RMSim/VehicleMujocoPhysics.cpp');
  native_bridge_source=(Join-Path $root 'unreal/RMSim/Source/RMSim/RosBridgeComponent.cpp')
 }
 $bindings=[ordered]@{}
 foreach($name in $inputs.Keys){$bindings[$name]=@{path=$inputs[$name];sha256=(Get-FileHash -LiteralPath $inputs[$name] -Algorithm SHA256).Hash.ToLowerInvariant()}}
 $report.inputs=$bindings;Save-Report
 Run-OwnedProbe $python @((Join-Path $root 'tools/mujoco/verify_model.py'),'--manifest',$manifest,'--map-file',$map,'--preflight-only','--output',(Join-Path $out 'model-preflight')) 'model-preflight'
 $arguments=@((Quote-UnrealArgument $ue.Project),'/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode',
  '-game','-RenderOffScreen','-unattended','-NoSplash','-NoSound','-ResX=1280','-ResY=800',
  '-PhysicsBackend=mujoco','-SimStepSeconds=0.005','-SimRealTimePace','-RMSimViewportHz=60',
  '-RosControl',('-RosPort='+$Port),'-RosCameraHz=0',
  ('-MujocoModel='+(Quote-UnrealArgument $manifest)),('-MeshRigFile='+(Quote-UnrealArgument $rig)),
  ('-MapFile='+(Quote-UnrealArgument $map)),('-abslog='+(Quote-UnrealArgument (Join-Path $out 'runtime.log'))))
 if((Get-Content -LiteralPath $arena -Raw|ConvertFrom-Json).render_profile -eq 'RMSimGymLumen'){
  $arguments+=@('-CustomConfig=RMSimGymLumen','-d3d12','-sm6')
 }else{$arguments+='-d3d11'}
 $report.command=@($ue.Editor)+$arguments;Save-Report
 $process=Start-Process -FilePath $ue.Editor -ArgumentList $arguments -WindowStyle Hidden -PassThru
 [void]$process.Handle;$report.pid=$process.Id;Save-Report
 Write-Output ('Owned shared-drive UE PID '+$process.Id)
 Run-OwnedProbe $python @((Join-Path $PSScriptRoot 'probe_shared_drive_profile.py'),'--port',$Port.ToString(),'--profile',$profile,'--output',(Join-Path $out 'tcp')) 'tcp-probe'
 $tcp=Get-Content -LiteralPath (Join-Path $out 'tcp/verification.json') -Raw -Encoding UTF8 | ConvertFrom-Json
 if($tcp.status -ne 'passed'){throw 'TCP shared-drive report is incomplete.'}
 if($tcp.profile_sha256 -ne $bindings.drive_profile.sha256){throw 'Probe source profile differs from launch profile.'}
 if($tcp.maximum_actual_planar_excursion_m -ge 1.0){throw 'Actual motion left the 1 m bounded drive area.'}
 $report.tcp_checks=@($tcp.checks).Count;$report.actual_planar_excursion_m=$tcp.maximum_actual_planar_excursion_m
 $runtime=Get-Content -LiteralPath (Join-Path $out 'runtime.log') -Raw -Encoding UTF8
 if($runtime -match 'Fatal error:|Assertion failed:|GPU Crashed|DXGI_ERROR_DEVICE_REMOVED'){throw 'Native runtime failure found in log.'}
 foreach($name in $inputs.Keys){
  if((Get-FileHash -LiteralPath $inputs[$name] -Algorithm SHA256).Hash.ToLowerInvariant() -ne $bindings[$name].sha256){throw ('Input changed during gate: '+$name)}
 }
 $report.status='passed'
}catch{
 $report.status='failed';$report.reason=$_.Exception.Message
 [Console]::Error.WriteLine($_.Exception.Message)
}finally{
 if($child){try{if(-not $child.HasExited){& taskkill.exe /PID $child.Id /T /F | Out-Null}}finally{$child.Dispose()}}
 if($process){try{if(-not $process.HasExited){Stop-Process -Id $process.Id -Force;[void]$process.WaitForExit(15000)}}finally{$process.Dispose()}}
 $report.finished_utc=[DateTime]::UtcNow.ToString('o');Save-Report
 if($locked){$mutex.ReleaseMutex()};$mutex.Dispose()
}
$report | ConvertTo-Json -Depth 16 -Compress
if($report.status -ne 'passed'){exit 3}
