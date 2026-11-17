param([Parameter(Mandatory=$true)][string]$EngineRoot,[Parameter(Mandatory=$true)][string]$EvidenceDirectory)
. (Join-Path $PSScriptRoot 'Common.ps1')
$ue=Get-UnrealTools -EngineRoot $EngineRoot;$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'));$out=[IO.Path]::GetFullPath($EvidenceDirectory)
if(Test-Path -LiteralPath $out){throw 'Use a fresh LiDAR occlusion directory.'}
New-Item -ItemType Directory -Path $out|Out-Null
foreach($mode in @('self','blind_low','blind_high')){
 $case=Join-Path $out $mode;New-Item -ItemType Directory -Path $case|Out-Null
 $profile=Get-Content -LiteralPath (Join-Path $root 'assets/vehicles/aim_sentry/sensors/lidar.json') -Raw -Encoding UTF8|ConvertFrom-Json
 $map=Get-Content -LiteralPath (Join-Path $root 'assets/maps/import_lab/map.json') -Raw -Encoding UTF8|ConvertFrom-Json
 if($mode -eq 'self'){$profile.origin.xyz=@(0,0,1);$profile.origin.rpy=@(0,([Math]::PI/2),0);$profile.min_range_m=.01}
 else{$map.obstacles+=@{id='near_lidar_occluder';center_m=@(1,0,1);size_m=@(.1,2,2);yaw_rad=0;color_rgb=@(.3,.3,.3)};if($mode -eq 'blind_high'){$profile.min_range_m=1.5}}
 $profile|ConvertTo-Json -Depth 20|Set-Content -LiteralPath (Join-Path $case 'lidar.json') -Encoding utf8
 $map|ConvertTo-Json -Depth 20|Set-Content -LiteralPath (Join-Path $case 'map.json') -Encoding utf8
 $arguments=@((Quote-UnrealArgument $ue.Project),'/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode','-game','-RenderOffScreen','-unattended','-NoSplash','-NoSound','-RosLidar','-SimStepSeconds=0.001','-RosPort=19490','-RosCameraWidth=320','-RosCameraHeight=240',
  ('-LidarProfile='+(Quote-UnrealArgument (Join-Path $case 'lidar.json'))),('-MeshRigFile='+(Quote-UnrealArgument (Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json'))),
  ('-MapFile='+(Quote-UnrealArgument (Join-Path $case 'map.json'))),('-abslog='+(Quote-UnrealArgument (Join-Path $case 'runtime.log'))))
 $process=Start-Process -FilePath $ue.Editor -ArgumentList $arguments -WindowStyle Hidden -PassThru;[void]$process.Handle
 try{
  $env:PYTHONPATH=Join-Path $root 'ros2_ws/src/rmsim_bridge'
  & python (Join-Path $root 'tools/integration/probe_lidar_occlusion.py') --port 19490 --mode $mode --output $case
  if($LASTEXITCODE -ne 0){throw ('LiDAR occlusion gate failed: '+$mode)}
 }finally{if(-not $process.HasExited){Stop-Process -Id $process.Id -Force}}
}
