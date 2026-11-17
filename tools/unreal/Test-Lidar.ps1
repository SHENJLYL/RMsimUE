param([Parameter(Mandatory=$true)][string]$EngineRoot,[Parameter(Mandatory=$true)][string]$EvidenceDirectory,[int]$Port=19481,[switch]$WithLio,[switch]$InteractiveReset,[string]$MapFile='',[string]$LidarProfile='',[switch]$MotorControl)
. (Join-Path $PSScriptRoot 'Common.ps1')
$ue=Get-UnrealTools -EngineRoot $EngineRoot;$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'));$out=[IO.Path]::GetFullPath($EvidenceDirectory)
if(Test-Path -LiteralPath $out){throw 'Use a fresh LiDAR evidence directory.'}
New-Item -ItemType Directory -Path $out|Out-Null
if($MapFile){$map=Get-Content -LiteralPath $MapFile -Raw -Encoding UTF8|ConvertFrom-Json}
else{$map=Get-Content -LiteralPath (Join-Path $root 'assets/maps/import_lab/map.json') -Raw -Encoding UTF8|ConvertFrom-Json;$map.boundary.height_m=3}
$map|ConvertTo-Json -Depth 40|Set-Content -LiteralPath (Join-Path $out 'map.json') -Encoding utf8
if(-not $LidarProfile){$LidarProfile=Join-Path $root 'assets/vehicles/aim_sentry/sensors/lidar.json'}
Copy-Item -LiteralPath $LidarProfile -Destination (Join-Path $out 'lidar.json')
@{started_utc=[DateTime]::UtcNow.ToString('o');
 module_sha256=(Get-FileHash -LiteralPath (Join-Path $root 'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll')).Hash;
 rig_sha256=(Get-FileHash -LiteralPath (Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json')).Hash;
 drive_profile_sha256=(Get-FileHash -LiteralPath (Join-Path $root 'assets/vehicles/aim_sentry/drive-profile.json')).Hash;
 map_sha256=(Get-FileHash -LiteralPath (Join-Path $out 'map.json')).Hash} |
 ConvertTo-Json | Set-Content -LiteralPath (Join-Path $out 'runtime-provenance.json') -Encoding utf8
$arguments=@((Quote-UnrealArgument $ue.Project),'/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode',
 '-game','-RenderOffScreen','-unattended','-NoSplash','-NoSound','-RosControl','-RosImu','-RosLidar',('-RosPort='+$Port),'-SimStepSeconds=0.001',
 '-RosCameraWidth=640','-RosCameraHeight=360',('-LidarProfile='+(Quote-UnrealArgument (Join-Path $out 'lidar.json'))),
 ('-MeshRigFile='+(Quote-UnrealArgument (Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json'))),
 ('-MapFile='+(Quote-UnrealArgument (Join-Path $out 'map.json'))),('-abslog='+(Quote-UnrealArgument (Join-Path $out 'runtime.log'))))
$process=$null
if($MotorControl){$arguments+='-RosMotorControl'}
if($InteractiveReset){$arguments=@($arguments|Where-Object {$_ -notin @('-RenderOffScreen','-unattended')});$arguments+=@('-windowed','-ResX=1600','-ResY=1000')}
try{
 if($InteractiveReset){$process=Start-Process -FilePath $ue.Editor -ArgumentList $arguments -PassThru}
 else{$process=Start-Process -FilePath $ue.Editor -ArgumentList $arguments -WindowStyle Hidden -PassThru}
 [void]$process.Handle
 Write-Output ('LiDAR server PID '+$process.Id)
 $client=@((Get-Process -Id $PID).Path,'-NoProfile','-File',(Join-Path $root 'tools/integration/Run-LidarProbe.ps1'),'-Port',$Port,'-OutputDirectory',(Join-Path $out 'ros'))
 if($WithLio){$client+='-WithLio'}
 if($InteractiveReset){$client+='-WaitReset'}
 if($MotorControl){$client+='-MotorControl'}
 & (Join-Path $root 'tools/environment/run-env.ps1') -Command $client
 if($LASTEXITCODE -ne 0){throw 'Native LiDAR gate failed.'}
}finally{
 if($process){try{if(-not $process.HasExited){Stop-Process -Id $process.Id -Force;if(-not $process.WaitForExit(15000)){throw 'Owned test server did not finish terminating.'}}}finally{$process.Dispose()}}
}
