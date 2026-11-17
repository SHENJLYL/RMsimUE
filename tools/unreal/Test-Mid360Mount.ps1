param([Parameter(Mandatory=$true)][string]$EngineRoot,[Parameter(Mandatory=$true)][string]$EvidenceDirectory,[int]$Port=19581)
. (Join-Path $PSScriptRoot 'Common.ps1')
$ue=Get-UnrealTools -EngineRoot $EngineRoot;$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$out=[IO.Path]::GetFullPath($EvidenceDirectory)
if(Test-Path $out){throw 'Use a fresh Mid-360 evidence directory.'}
$live=@(Get-CimInstance Win32_Process -Filter "Name='UnrealEditor.exe'" | Where-Object {$_.CommandLine -and $_.CommandLine.Replace('\','/').Contains('simulator/unreal/RMSim/RMSim.uproject')})
if($live.Count){throw 'Close the existing RMSim instance before this sensor test.'}
New-Item -ItemType Directory -Path $out | Out-Null
$map=Get-Content (Join-Path $root 'assets/maps/import_lab/map.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$map.boundary.height_m=3
$map | ConvertTo-Json -Depth 40 | Set-Content (Join-Path $out 'map.json') -Encoding UTF8
$lidar=Join-Path $root 'assets/vehicles/aim_sentry/sensors/mid360.json';$imu=Join-Path $root 'assets/vehicles/aim_sentry/sensors/mid360-imu.json'
$provenance=@{started_utc=[DateTime]::UtcNow.ToString('o');module_sha256=(Get-FileHash (Join-Path $root 'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll')).Hash;lidar_sha256=(Get-FileHash $lidar).Hash;imu_sha256=(Get-FileHash $imu).Hash;drive_profile_sha256=(Get-FileHash (Join-Path $root 'assets/vehicles/aim_sentry/drive-profile.json')).Hash}
$provenance | ConvertTo-Json | Set-Content (Join-Path $out 'runtime-provenance.json') -Encoding UTF8
$arguments=@((Quote-UnrealArgument $ue.Project),'/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode','-game','-RenderOffScreen','-unattended','-NoSplash','-NoSound','-RosControl','-RosAimControl','-RosImu','-RosLidar','-SimStepSeconds=0.001','-RosCameraWidth=320','-RosCameraHeight=240',('-RosPort='+$Port),('-LidarProfile='+(Quote-UnrealArgument $lidar)),('-ImuProfile='+(Quote-UnrealArgument $imu)),('-MeshRigFile='+(Quote-UnrealArgument (Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json'))),('-MapFile='+(Quote-UnrealArgument (Join-Path $out 'map.json'))),('-abslog='+(Quote-UnrealArgument (Join-Path $out 'runtime.log'))))
$process=$null
try{
 $process=Start-Process $ue.Editor -ArgumentList $arguments -WindowStyle Hidden -PassThru;[void]$process.Handle
 Write-Output ('Mid-360 native server PID '+$process.Id)
 & python (Join-Path $root 'tools/integration/probe_mid360_mount.py') --port $Port --output (Join-Path $out 'tcp')
 if($LASTEXITCODE -ne 0){throw 'Articulated Mid-360 native checks failed.'}
}finally{
 if($process){try{if(-not $process.HasExited){Stop-Process -Id $process.Id -Force;[void]$process.WaitForExit(15000)}}finally{$process.Dispose()}}
}
