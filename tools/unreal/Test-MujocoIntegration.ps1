param(
 [Parameter(Mandatory=$true)][string]$EngineRoot,
 [Parameter(Mandatory=$true)][string]$ModelManifest,
 [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
 [string]$MapFile='',
 [string]$SpawnId='default',
 [double]$RouteCm=100,
 [double]$GimbalYaw=0,
 [double]$GimbalPitch=0,
 [switch]$AllowDiagnosticPlane,
 [switch]$RosSensors,
 [int]$RosPort=9463,
 [int]$TimeoutSeconds=240,
 [int]$WaitForIdleSeconds=0
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'SceneMap.ps1')
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$out=[IO.Path]::GetFullPath($EvidenceDirectory)
if(Test-Path -LiteralPath $out){throw 'Use a fresh MuJoCo evidence directory.'}
New-Item -ItemType Directory -Path $out | Out-Null
$started=[DateTime]::UtcNow
$locked=$false
$mutex=$null
$process=$null
$report=[ordered]@{status='started';started_utc=$started.ToString('o');scope='native UE MuJoCo integration; Windows; shared scripted controls';os_input_verified=$false}
try {
 $ue=Get-UnrealTools -EngineRoot $EngineRoot
 $keyText=$ue.Project.Replace('\','/').ToLowerInvariant()
 $sha=[Security.Cryptography.SHA256]::Create()
 try{$key=([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($keyText)))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
 $mutex=[Threading.Mutex]::new($false,'Local\RMSimSceneImport_'+$key)
 try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
 if(-not $locked){throw 'RMSim build/import/launch is already active.'}
 $idleDeadline=[DateTime]::UtcNow.AddSeconds($WaitForIdleSeconds)
 $idleSince=$null
 do {
  $active=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match 'UnrealEditor|UnrealBuildTool|dotnet' -and $_.CommandLine -and $_.CommandLine.Replace('\','/').ToLowerInvariant().Contains($keyText)})
  if($active.Count){$idleSince=$null}else{if($null -eq $idleSince){$idleSince=[DateTime]::UtcNow}}
  $stableIdle=$active.Count -eq 0 -and ($WaitForIdleSeconds -eq 0 -or ([DateTime]::UtcNow-$idleSince).TotalSeconds -ge 4)
  if($stableIdle){break}
  if([DateTime]::UtcNow -ge $idleDeadline){throw ('RMSim remained busy; last observed PID '+(($active|ForEach-Object ProcessId)-join ','))}
  Start-Sleep -Milliseconds 750
 } while($true)
 $manifest=(Resolve-Path -LiteralPath $ModelManifest).Path
 $model=Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
 if($model.schema -ne 'rmsim_mujoco_scene_v1'){throw 'Invalid MuJoCo manifest schema.'}
 if($model.diagnostic_plane_only -and -not $AllowDiagnosticPlane){throw 'Diagnostic plane requires explicit AllowDiagnosticPlane.'}
 if(-not $MapFile){$MapFile=Join-Path $root 'assets/maps/import_lab/map.json'}
 $mapLaunch=Get-RMSimMapLaunch -MapFile $MapFile -Project $ue.Project -SpawnId $SpawnId
 $inputPath=Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json'
 $arguments=@((Quote-UnrealArgument $ue.Project),$mapLaunch.WorldUrl,
  '-game','-RenderOffScreen','-unattended','-NoSplash','-NoSound','-ResX=1280','-ResY=800',
  '-ContactProbe','-MujocoProbe','-PhysicsBackend=mujoco','-SimStepSeconds=0.005',
  ('-MujocoModel='+(Quote-UnrealArgument $manifest)),
  ('-MujocoRouteCm='+$RouteCm.ToString([Globalization.CultureInfo]::InvariantCulture)),
  ('-GimbalYaw='+$GimbalYaw.ToString([Globalization.CultureInfo]::InvariantCulture)),
  ('-GimbalPitch='+$GimbalPitch.ToString([Globalization.CultureInfo]::InvariantCulture)),
  ('-MapFile='+(Quote-UnrealArgument $mapLaunch.MapFile)),
  ('-SpawnId='+(Quote-UnrealArgument $SpawnId)),
  ('-MeshRigFile='+(Quote-UnrealArgument $inputPath)),
  ('-EvidenceDir='+(Quote-UnrealArgument $out)),
  ('-abslog='+(Quote-UnrealArgument (Join-Path $out 'runtime.log'))))
 $arguments+=@($mapLaunch.RuntimeArguments)
 if($AllowDiagnosticPlane){$arguments+='-MujocoAllowDiagnosticPlane'}
 if($RosSensors){$arguments+=@('-RosBridge','-RosImu','-RosLidar',('-RosPort='+$RosPort))}
 $arena=Join-Path (Split-Path -Parent $mapLaunch.MapFile) 'arena-shell.json'
 if((Test-Path -LiteralPath $arena) -and ((Get-Content -LiteralPath $arena -Raw|ConvertFrom-Json).render_profile -eq 'RMSimGymLumen')){
  $arguments+=@('-CustomConfig=RMSimGymLumen','-d3d12','-sm6')
 }else{$arguments+='-d3d11'}
 $report.command=@($ue.Editor)+$arguments
 $report.manifest_sha256=(Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash.ToLowerInvariant()
 $report.module_sha256=(Get-FileHash -LiteralPath (Join-Path (Split-Path -Parent $ue.Project) 'Binaries/Win64/UnrealEditor-RMSim.dll') -Algorithm SHA256).Hash.ToLowerInvariant()
 $report.runtime_sha256=(Get-FileHash -LiteralPath (Join-Path (Split-Path -Parent $ue.Project) 'Binaries/Win64/mujoco.dll') -Algorithm SHA256).Hash.ToLowerInvariant()
 $report.map_sha256=(Get-FileHash -LiteralPath $mapLaunch.MapFile -Algorithm SHA256).Hash.ToLowerInvariant()
 $report.diagnostic_plane_only=[bool]$model.diagnostic_plane_only
 $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $out 'verification.json') -Encoding UTF8
 $process=Start-Process -FilePath $ue.Editor -ArgumentList $arguments -WindowStyle Hidden -PassThru
 [void]$process.Handle
 $report.pid=$process.Id
 $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $out 'verification.json') -Encoding UTF8
 Write-Output "MuJoCo integration probe PID $($process.Id)"
 if(-not $process.WaitForExit($TimeoutSeconds*1000)){
  Stop-Process -Id $process.Id -Force
  throw 'MuJoCo probe timed out; incomplete evidence retained.'
 }
 $process.WaitForExit();$process.Refresh()
 $report.exit_code=$process.ExitCode
 if($null -eq $process.ExitCode -or $process.ExitCode -ne 0){throw "Native process failed: $($process.ExitCode)"}
 $control=Get-Content -LiteralPath (Join-Path $out 'control-result.json') -Raw -Encoding UTF8 | ConvertFrom-Json
 $expected=@('mujoco_settle_with_real_contact','mujoco_wheel_contact_forward','mujoco_physical_brake',
  'mujoco_wheel_contact_reverse','mujoco_positive_yaw','mujoco_negative_yaw','mujoco_swerve_lateral_contact',
  'mujoco_articulated_gimbal_and_sensor_mount','mujoco_shared_R_resets_solver_and_commands')
 if($control.status -ne 'passed' -or -not $control.complete -or @($control.checks).Count -ne $expected.Count){throw 'Native report is incomplete or failed.'}
 foreach($name in $expected){
  $matches=@($control.checks|Where-Object name -eq $name)
  if($matches.Count -ne 1 -or -not $matches[0].passed){throw "Missing/failed check: $name"}
 }
 Add-Type -AssemblyName System.Drawing
 $captures=@()
 foreach($phase in @('mujoco-rest','mujoco-forward','mujoco-return','mujoco-gimbal','mujoco-reset')){
  foreach($camera in @('overview','aim_camera')){
   $path=Join-Path $out "$phase/$camera.png"
   $img=[Drawing.Image]::FromFile($path)
   try{
    if($img.RawFormat.Guid -ne [Drawing.Imaging.ImageFormat]::Png.Guid -or $img.Width -lt 1){throw "Invalid PNG: $path"}
    $captures+=@{path="$phase/$camera.png";width=$img.Width;height=$img.Height;sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()}
   }finally{$img.Dispose()}
  }
 }
 $report.status='passed';$report.checks=$expected;$report.captures=$captures
 $report.physics_backend=$control.physics_backend
 $report.dynamics_step_s=$control.dynamics_step_s
 $report.run_id=$control.run_id
 Write-Output "MuJoCo native integration PASS: $($expected.Count) checks, $($captures.Count) decoded captures."
}catch{
 $report.status='failed';$report.reason=$_.Exception.Message
 [Console]::Error.WriteLine($_.Exception.Message)
}finally{
 $report.finished_utc=[DateTime]::UtcNow.ToString('o')
 $report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $out 'verification.json') -Encoding UTF8
 if($locked){$mutex.ReleaseMutex()}
 if($mutex){$mutex.Dispose()}
}
if($report.status -ne 'passed'){exit 3}
