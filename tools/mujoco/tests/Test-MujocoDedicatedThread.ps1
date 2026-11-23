param(
 [Parameter(Mandatory=$true)][string]$EngineRoot,
 [Parameter(Mandatory=$true)][string]$EvidenceDirectory
)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
. (Join-Path $root 'tools/unreal/Common.ps1')
$ue=Get-UnrealTools -EngineRoot $EngineRoot
$out=[IO.Path]::GetFullPath($EvidenceDirectory)
if(Test-Path -LiteralPath $out){throw 'Use a new dedicated thread evidence directory.'}
New-Item -ItemType Directory -Path $out | Out-Null
$report=[ordered]@{status='running';scope='native Windows solver, scripted game-thread stall and reset';
 module_sha256=(Get-FileHash -LiteralPath (Join-Path $root 'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll')).Hash}
$project=$ue.Project.Replace('\','/').ToLowerInvariant()
$existing=Get-CimInstance Win32_Process -Filter "Name='UnrealEditor.exe'" | Where-Object {
 $_.CommandLine -and $_.CommandLine.Replace('\','/').ToLowerInvariant().Contains($project)
}
if($existing){throw 'The project is already in use.'}
$rig=Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json'
$map=Join-Path $root 'assets/maps/rmuc2027/map.json'
$model=Join-Path $root 'assets/vehicles/aim_sentry/mujoco/rmuc2027/manifest.json'
$log=Join-Path $out 'runtime.log'
$arguments=@((Quote-UnrealArgument $ue.Project),'/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode',
 '-game','-RenderOffScreen','-nullrhi','-unattended','-NoSplash','-NoSound',
 '-MujocoThreadProof','-PhysicsBackend=mujoco','-SimStepSeconds=0.005','-SimRealTimePace','-RMSimViewportHz=120',
 ('-MeshRigFile='+(Quote-UnrealArgument $rig)),('-MapFile='+(Quote-UnrealArgument $map)),
 ('-MujocoModel='+(Quote-UnrealArgument $model)),('-abslog='+(Quote-UnrealArgument $log)))
$report.command=$arguments
$process=$null
try {
 $process=Start-Process -FilePath $ue.Editor -ArgumentList $arguments -WindowStyle Hidden -PassThru
 [void]$process.Handle
 Write-Output "Dedicated solver proof PID $($process.Id)"
 if(-not $process.WaitForExit(180000)){throw 'Dedicated thread proof timed out.'}
 $process.WaitForExit();$process.Refresh();$report.exit_code=$process.ExitCode
 if($process.ExitCode -ne 0){throw 'Native process exited with a failure.'}
 $text=Get-Content -LiteralPath $log -Raw -Encoding UTF8
 $report.stall=[regex]::Match($text,'MUJOCO_THREAD_PROOF_STALL[^\r\n]+').Value
 $report.reset=[regex]::Match($text,'MUJOCO_THREAD_PROOF_PASSED[^\r\n]+').Value
 $report.worker_start_count=[regex]::Matches($text,'MUJOCO_DEDICATED_READY').Count
 $report.worker_shutdown_count=[regex]::Matches($text,'MUJOCO_DEDICATED_STOP').Count
 if(-not $report.stall -or -not $report.reset -or $report.worker_start_count -ne 2 -or $report.worker_shutdown_count -ne 2){
  throw 'Readiness, independent stall, reset or joined shutdown evidence is missing.'
 }
 $report.status='passed'
} catch {
 $report.status='failed';$report.reason=$_.Exception.Message
 [Console]::Error.WriteLine($report.reason)
} finally {
 if($process){if(-not $process.HasExited){Stop-Process -Id $process.Id -Force};$process.Dispose()}
 $report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $out 'verification.json') -Encoding utf8
}
$report | ConvertTo-Json -Depth 5 -Compress
if($report.status -ne 'passed'){exit 1}
