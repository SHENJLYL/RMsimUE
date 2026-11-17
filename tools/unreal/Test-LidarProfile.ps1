param([Parameter(Mandatory=$true)][string]$EngineRoot,[Parameter(Mandatory=$true)][string]$EvidenceDirectory)
. (Join-Path $PSScriptRoot 'Common.ps1')
$ue=Get-UnrealTools -EngineRoot $EngineRoot;$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'));$out=[IO.Path]::GetFullPath($EvidenceDirectory)
if(Test-Path -LiteralPath $out){throw 'Use a fresh invalid LiDAR profile directory.'}
New-Item -ItemType Directory -Path $out|Out-Null
$checks=@()
foreach($case in @('invalid_rate','duplicate_sensor_id')){
 $profile=Get-Content -LiteralPath (Join-Path $root 'assets/vehicles/aim_sentry/sensors/lidar.json') -Raw -Encoding UTF8|ConvertFrom-Json
 $errorText=if($case -eq 'invalid_rate'){$profile.frame_hz=0;'invalid LiDAR identity, mount, scan or range configuration'}else{$profile.id='imu';'IMU and LiDAR sensor ids must be unique'}
 $file=Join-Path $out ($case+'.json');$profile|ConvertTo-Json -Depth 20|Set-Content -LiteralPath $file -Encoding utf8
 $log=Join-Path $out ($case+'.log')
 $arguments=@((Quote-UnrealArgument $ue.Project),'/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode','-game','-RenderOffScreen','-unattended','-NoSplash','-NoSound','-RosImu','-RosLidar','-RosPort=19489',
  ('-LidarProfile='+(Quote-UnrealArgument $file)),('-MeshRigFile='+(Quote-UnrealArgument (Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json'))),
  ('-MapFile='+(Quote-UnrealArgument (Join-Path $root 'assets/maps/import_lab/map.json'))),('-abslog='+(Quote-UnrealArgument $log)))
 $p=Start-Process -FilePath $ue.Editor -ArgumentList $arguments -WindowStyle Hidden -PassThru;[void]$p.Handle
 try{
  if(-not $p.WaitForExit(60000)){throw 'Invalid LiDAR profile did not terminate'}
  $p.WaitForExit();$p.Refresh();$text=Get-Content -LiteralPath $log -Raw -Encoding UTF8
  if($null -eq $p.ExitCode -or -not $text.Contains('IMPORT_LAB_FAILED '+$errorText) -or $text.Contains('ROS_BRIDGE_READY')){throw 'Invalid LiDAR profile rejection not observed'}
  $checks+=@{name=$case;passed=$true;process_exit=$p.ExitCode;error=$errorText}
 }finally{if(-not $p.HasExited){Stop-Process -Id $p.Id -Force}}
}
@{passed=$true;checks=$checks;scope='actual startup rejection and termination; exit status alone is not acceptance'}|ConvertTo-Json -Depth 10|Set-Content -LiteralPath (Join-Path $out 'verification.json') -Encoding utf8
Write-Output 'LiDAR profile rejection PASS 2 cases'
