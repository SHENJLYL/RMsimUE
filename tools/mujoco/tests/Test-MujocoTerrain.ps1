param(
 [Parameter(Mandatory=$true)][string]$EngineRoot,
 [Parameter(Mandatory=$true)][ValidateSet('WaveRed','WaveBlue','Boundary')][string]$Gate,
 [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
 [string]$ModelManifest='',
 [int]$TimeoutSeconds=300
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
if(-not $ModelManifest){$ModelManifest=Join-Path $root 'assets/vehicles/aim_sentry/mujoco/rmuc2027/manifest.json'}
$out=[IO.Path]::GetFullPath($EvidenceDirectory)
if(Test-Path -LiteralPath $out){throw 'Use a fresh formal RMUC evidence directory.'}
New-Item -ItemType Directory -Path $out | Out-Null
$project=(Join-Path $root 'unreal/RMSim/RMSim.uproject').Replace('\','/').ToLowerInvariant()
$sha=[Security.Cryptography.SHA256]::Create()
try{$key=([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($project)))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
$mutex=[Threading.Mutex]::new($false,'Local\RMSimSceneImport_'+$key)
$locked=$false;$process=$null;$code=3
$report=[ordered]@{status='started';gate=$Gate;started_utc=[DateTime]::UtcNow.ToString('o');scope='Native MuJoCo on unchanged source RMUC geometry; copied map changes spawn only'}
try {
 try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
 if(-not $locked){throw 'RMSim build/import/launch is already active.'}
 $active=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match 'UnrealEditor|UnrealBuildTool|dotnet' -and $_.CommandLine -and $_.CommandLine.Replace('\','/').ToLowerInvariant().Contains($project)})
 if($active.Count){throw ('RMSim is in use by PID '+(($active|ForEach-Object ProcessId)-join ','))}
 $manifest=(Resolve-Path -LiteralPath $ModelManifest).Path
 $model=Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
 if($model.diagnostic_plane_only -or $model.runtime_bindings.Count -ne 33){throw 'Formal RMUC requires the sealed full-field model.'}
 $map=Get-Content -LiteralPath (Join-Path $root 'assets/maps/rmuc2027/map.json') -Raw -Encoding UTF8 | ConvertFrom-Json
 $spawn=[ordered]@{id='mujoco_probe';team='neutral';position_m=@(0.0,0.0,0.0);yaw_rad=0.0}
 if($Gate -eq 'WaveRed'){$spawn.position_m=@(5.8,6.4,0.0)}
 elseif($Gate -eq 'WaveBlue'){$spawn.position_m=@(-5.8,-6.4,0.0);$spawn.yaw_rad=[Math]::PI}
 else{$spawn.position_m=@(-11.0,-6.6,0.0);$spawn.yaw_rad=-[Math]::PI/2}
 $map.spawns=@($map.spawns)+@($spawn)
 $mapPath=Join-Path $out 'scene.json'
 $map | ConvertTo-Json -Depth 60 | Set-Content -LiteralPath $mapPath -Encoding UTF8
 $copyHelper=Join-Path $out 'copy_arena.py'
 @'
from pathlib import Path
import json,sys
root=Path(sys.argv[1])
sys.path.insert(0,str(root/'tools/integration'))
from navigation_console_config import copy_runtime_arena
print(json.dumps(copy_runtime_arena(root/'assets/maps/rmuc2027/arena-shell.json',Path(sys.argv[2]))))
'@ | Set-Content -LiteralPath $copyHelper -Encoding UTF8
 $arenaSnapshot=& python $copyHelper $root $out
 if($LASTEXITCODE -ne 0){throw 'Could not snapshot the source arena dependencies.'}
 $report.arena_snapshot=$arenaSnapshot | ConvertFrom-Json
 $report.gate_sha256=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
 $native=Join-Path $out 'native'
 New-Item -ItemType Directory -Path $native | Out-Null
 . (Join-Path $root 'tools/unreal/Common.ps1')
 $ue=Get-UnrealTools -EngineRoot $EngineRoot
 $arguments=@((Quote-UnrealArgument $ue.Project),'/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode',
  '-game','-RenderOffScreen','-unattended','-NoSplash','-NoSound','-ResX=1280','-ResY=800',
  '-ContactProbe','-PhysicsBackend=mujoco','-SimStepSeconds=0.005','-RMSimViewportHz=60','-CustomConfig=RMSimGymLumen','-d3d12','-sm6',
  ('-MujocoModel='+(Quote-UnrealArgument $manifest)),('-MapFile='+(Quote-UnrealArgument $mapPath)),
  '-SpawnId=mujoco_probe',('-MeshRigFile='+(Quote-UnrealArgument (Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json'))),
  ('-EvidenceDir='+(Quote-UnrealArgument $native)),('-abslog='+(Quote-UnrealArgument (Join-Path $native 'runtime.log'))))
 if($Gate -eq 'Boundary'){$arguments+='-CampusGymBoundaryProbe'}
 else{$arguments+=@('-ContactDistanceCm=340','-ContactSpeedMps=1')}
 $report.command=@($ue.Editor)+$arguments
 $report.spawn=$spawn
 $report.manifest_sha256=(Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash.ToLowerInvariant()
 $report.model_sha256=$model.model_sha256
 $report.physics_identity_sha256=$model.physics_identity_sha256
 $report.module_sha256=(Get-FileHash -LiteralPath (Join-Path $root 'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll') -Algorithm SHA256).Hash.ToLowerInvariant()
 $report.runtime_sha256=(Get-FileHash -LiteralPath (Join-Path $root 'unreal/RMSim/Binaries/Win64/mujoco.dll') -Algorithm SHA256).Hash.ToLowerInvariant()
 $report.map_sha256=(Get-FileHash -LiteralPath $mapPath -Algorithm SHA256).Hash.ToLowerInvariant()
 $report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $out 'verification.json') -Encoding UTF8
 $process=Start-Process -FilePath $ue.Editor -ArgumentList $arguments -WindowStyle Hidden -PassThru
 [void]$process.Handle;$report.pid=$process.Id
 $report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $out 'verification.json') -Encoding UTF8
 Write-Output "Formal RMUC $Gate probe PID $($process.Id)"
 if(-not $process.WaitForExit($TimeoutSeconds*1000)){Stop-Process -Id $process.Id -Force;[void]$process.WaitForExit(15000);throw 'Owned formal RMUC probe timed out; incomplete evidence retained.'}
 $process.WaitForExit();$process.Refresh();$report.exit_code=$process.ExitCode
 if($null -eq $process.ExitCode -or $process.ExitCode -ne 0){throw "Native formal RMUC process failed: $($process.ExitCode)"}
 $control=Get-Content -LiteralPath (Join-Path $native 'control-result.json') -Raw -Encoding UTF8 | ConvertFrom-Json
 $expected=@('physical_settle','physical_forward_route','physical_brake','physical_reverse_route')
 $phases=@('contact-rest','contact-end','contact-return')
 if($Gate -eq 'Boundary'){
  $expected=@('physical_settle','actual_closed_perimeter_contact','closed_glass_boundary_blocks_continued_drive','physical_brake_at_boundary','physical_reverse_releases_wall_contact','final_stop_inside_platform_boundary')
  $phases=@('gym-boundary-before','gym-boundary-blocked','gym-boundary-reverse')
 }
 if($control.status -ne 'passed' -or -not $control.complete -or @($control.checks).Count -ne $expected.Count){throw 'Formal native report incomplete/failed.'}
 foreach($name in $expected){$found=@($control.checks|Where-Object name -eq $name);if($found.Count -ne 1 -or -not $found[0].passed){throw "Native check missing/failed: $name"}}
 $report.native_checks=@($control.checks|Select-Object name,passed,detail)
 $report.run_id=$control.run_id
 Add-Type -AssemblyName System.Drawing
 $captures=@()
 foreach($phase in $phases){foreach($camera in @('overview','aim_camera')){
  $path=Join-Path $native "$phase/$camera.png";$img=[Drawing.Image]::FromFile($path)
  try{if($img.RawFormat.Guid -ne [Drawing.Imaging.ImageFormat]::Png.Guid -or $img.Width -lt 1){throw "Invalid PNG $path"};$captures+=@{path="native/$phase/$camera.png";width=$img.Width;height=$img.Height;sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()}}finally{$img.Dispose()}
 }}
 $report.captures=$captures;$report.status='passed';$code=0
 Write-Output "Formal RMUC $Gate passed $($expected.Count) native checks, $($captures.Count) PNGs."
} catch {
 $report.status='failed';$report.reason=$_.Exception.Message
 [Console]::Error.WriteLine($_.Exception.Message)
} finally {
 $report.finished_utc=[DateTime]::UtcNow.ToString('o')
 $report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $out 'verification.json') -Encoding UTF8
 if($process){try{if(-not $process.HasExited){$process.Kill();[void]$process.WaitForExit(15000)}}finally{$process.Dispose()}}
 if($locked){$mutex.ReleaseMutex()};$mutex.Dispose()
}
$report | ConvertTo-Json -Depth 12 -Compress
exit $code
