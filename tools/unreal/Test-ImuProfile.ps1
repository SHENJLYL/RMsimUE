param([Parameter(Mandatory=$true)][string]$EngineRoot,[Parameter(Mandatory=$true)][string]$EvidenceDirectory)
. (Join-Path $PSScriptRoot 'Common.ps1')
$ue=Get-UnrealTools -EngineRoot $EngineRoot;$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$out=[IO.Path]::GetFullPath($EvidenceDirectory)
if(Test-Path -LiteralPath $out){throw 'Use a fresh invalid-profile directory.'}
New-Item -ItemType Directory -Path $out | Out-Null
$profile=Get-Content (Join-Path $root 'assets/vehicles/aim_sentry/sensors/imu.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$profile.imus[0].accel_noise_std_mps2=@(-1,0,0)
$file=Join-Path $out 'invalid.json';$profile|ConvertTo-Json -Depth 20|Set-Content $file -Encoding utf8
$log=Join-Path $out 'runtime.log'
$argsList=@((Quote-UnrealArgument $ue.Project),'/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode','-game','-RenderOffScreen','-unattended','-NoSplash','-NoSound','-RosImu','-RosPort=19475',
 ('-ImuProfile='+(Quote-UnrealArgument $file)),('-MeshRigFile='+(Quote-UnrealArgument (Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json'))),
 ('-MapFile='+(Quote-UnrealArgument (Join-Path $root 'assets/maps/import_lab/map.json'))),('-abslog='+(Quote-UnrealArgument $log)))
$process=Start-Process -FilePath $ue.Editor -ArgumentList $argsList -WindowStyle Hidden -PassThru;[void]$process.Handle
try{
 if(-not $process.WaitForExit(60000)){throw 'Invalid profile did not terminate.'}
 $process.WaitForExit();$process.Refresh()
 $text=Get-Content $log -Raw
 if($null -eq $process.ExitCode -or $text -notmatch 'IMPORT_LAB_FAILED invalid IMU identity, parent, rate, noise or bias' -or $text -match 'IMU_READY|ROS_BRIDGE_READY'){throw 'Invalid profile rejection was not observed.'}
 @{passed=$true;process_exit=$process.ExitCode;case='negative noise standard deviation';scope='actual rejection log and process termination; Windows graceful exit code alone is not acceptance'}|ConvertTo-Json|Set-Content (Join-Path $out 'verification.json') -Encoding utf8
 Write-Output 'Native invalid IMU profile rejection passed.'
}finally{if(-not $process.HasExited){Stop-Process -Id $process.Id -Force}}
