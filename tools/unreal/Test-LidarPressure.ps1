param([Parameter(Mandatory=$true)][string]$EngineRoot,[Parameter(Mandatory=$true)][string]$EvidenceDirectory,[int]$Port=19485)
. (Join-Path $PSScriptRoot 'Common.ps1')
$ue=Get-UnrealTools -EngineRoot $EngineRoot;$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'));$out=[IO.Path]::GetFullPath($EvidenceDirectory)
if(Test-Path -LiteralPath $out){throw 'Use a fresh LiDAR pressure directory.'}
New-Item -ItemType Directory -Path $out|Out-Null
$profile=Get-Content -LiteralPath (Join-Path $root 'assets/vehicles/aim_sentry/sensors/lidar.json') -Raw -Encoding UTF8|ConvertFrom-Json
$profile.frame_hz=30
$file=Join-Path $out 'lidar-30hz.json';$profile|ConvertTo-Json -Depth 20|Set-Content -LiteralPath $file -Encoding utf8
$arguments=@((Quote-UnrealArgument $ue.Project),'/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode','-game','-RenderOffScreen','-unattended','-NoSplash','-NoSound',
 '-RosControl','-RosImu','-RosLidar','-SimStepSeconds=0.001',('-RosPort='+$Port),'-RosCameraWidth=640','-RosCameraHeight=360',
 ('-LidarProfile='+(Quote-UnrealArgument $file)),('-MeshRigFile='+(Quote-UnrealArgument (Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json'))),
 ('-MapFile='+(Quote-UnrealArgument (Join-Path $root 'assets/maps/import_lab/map.json'))),('-abslog='+(Quote-UnrealArgument (Join-Path $out 'runtime.log'))))
$process=Start-Process -FilePath $ue.Editor -ArgumentList $arguments -WindowStyle Hidden -PassThru;[void]$process.Handle
try{
 $env:PYTHONPATH=Join-Path $root 'ros2_ws/src/rmsim_bridge'
 & python (Join-Path $root 'tools/integration/probe_lidar_pressure.py') --port $Port --output (Join-Path $out 'tcp')
 if($LASTEXITCODE -ne 0){throw 'LiDAR pressure gate failed.'}
}finally{if(-not $process.HasExited){Stop-Process -Id $process.Id -Force}}
