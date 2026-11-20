param(
 [Parameter(Mandatory=$true)][string]$EngineRoot,
 [Parameter(Mandatory=$true)][string]$SessionDirectory,
 [int]$Port=9474,
 [int]$Domain=174,
 [int]$ProfileFrames=0,
 [switch]$ProfileSensorTiming,
 [switch]$ProfileMujocoTiming
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
. (Join-Path $root 'tools/unreal/Common.ps1')
$ue=Get-UnrealTools -EngineRoot $EngineRoot
$project=$ue.Project.Replace('\','/').ToLowerInvariant()
$sha=[Security.Cryptography.SHA256]::Create()
try{$key=([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($project)))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
$mutex=[Threading.Mutex]::new($false,'Local\RMSimSceneImport_'+$key)
$locked=$false;$exitCode=3
try{
 try{$locked=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$locked=$true}
 if(-not $locked){throw 'RMSim shared build/import/launch lock is held.'}
 $active=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match 'UnrealEditor|UnrealBuildTool|dotnet' -and $_.CommandLine -and $_.CommandLine.Replace('\','/').ToLowerInvariant().Contains($project)})
 if($active.Count){throw ('RMSim is in use by PID '+(($active|ForEach-Object ProcessId)-join ','))}
 $python=Join-Path $root 'artifacts/environments/mujoco-4.0.1/Scripts/python.exe'
 $arguments=@((Join-Path $PSScriptRoot 'run_mujoco_navigation_smoke.py'),'--session',([IO.Path]::GetFullPath($SessionDirectory)),'--engine-root',$EngineRoot,'--port',$Port,'--domain',$Domain,'--profile-frames',$ProfileFrames)
 if($ProfileSensorTiming){$arguments+='--profile-sensor-timing'}
 if($ProfileMujocoTiming){$arguments+='--profile-mujoco-timing'}
 & $python @arguments
 $exitCode=$LASTEXITCODE
}finally{
 if($locked){$mutex.ReleaseMutex()}
 $mutex.Dispose()
}
exit $exitCode
