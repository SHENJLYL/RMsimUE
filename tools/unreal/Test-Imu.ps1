param(
    [Parameter(Mandatory=$true)][string]$EngineRoot,
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
    [ValidateSet('motion','freefall','incline')][string]$Fixture='motion',
    [double]$SimStepSeconds=.001,
    [int]$Port=19471,
    [switch]$InteractiveReset
)
. (Join-Path $PSScriptRoot 'Common.ps1')
$ue=Get-UnrealTools -EngineRoot $EngineRoot
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'));$out=[IO.Path]::GetFullPath($EvidenceDirectory)
if(Test-Path -LiteralPath $out){throw 'Use a fresh IMU evidence directory.'}
New-Item -ItemType Directory -Path $out | Out-Null
$profile=Get-Content (Join-Path $root 'assets/vehicles/aim_sentry/sensors/imu.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$base=$profile.imus[0]
$offset=($base|ConvertTo-Json -Depth 10|ConvertFrom-Json);$offset.id='imu_offset';$offset.origin.xyz=@(.2,0,.15)
$rotated=($base|ConvertTo-Json -Depth 10|ConvertFrom-Json);$rotated.id='imu_rotated';$rotated.origin.rpy=@(0,([Math]::PI/2),0)
$noisy=($base|ConvertTo-Json -Depth 10|ConvertFrom-Json);$noisy.id='imu_noisy';$noisy.noise_seed=4054
$noisy.accel_bias_mps2=@(.02,-.01,.03);$noisy.gyro_bias_rad_s=@(.001,-.002,.003)
$noisy.accel_noise_std_mps2=@(.01,.01,.01);$noisy.gyro_noise_std_rad_s=@(.0005,.0005,.0005)
$profile.imus=@($base,$offset,$rotated,$noisy)
$profilePath=Join-Path $out 'imu.json';$profile|ConvertTo-Json -Depth 20|Set-Content $profilePath -Encoding utf8
$arguments=@((Quote-UnrealArgument $ue.Project),'/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode',
 '-game','-RenderOffScreen','-unattended','-NoSplash','-NoSound','-RosControl','-RosImu',('-RosPort='+$Port),
 '-RosCameraWidth=640','-RosCameraHeight=360',('-SimStepSeconds='+$SimStepSeconds.ToString([Globalization.CultureInfo]::InvariantCulture)),
 ('-ImuProfile='+(Quote-UnrealArgument $profilePath)),
 ('-MeshRigFile='+(Quote-UnrealArgument (Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json'))),
 ('-MapFile='+(Quote-UnrealArgument (Join-Path $root 'assets/maps/import_lab/map.json'))),
 ('-abslog='+(Quote-UnrealArgument (Join-Path $out 'runtime.log'))))
if($Fixture -ne 'motion'){$arguments+='-ImuFixture='+$Fixture}
if($InteractiveReset){$arguments=@($arguments|Where-Object {$_ -notin @('-RenderOffScreen','-unattended')});$arguments+=@('-windowed','-ResX=1600','-ResY=1000')}
$process=$null
try{
 if($InteractiveReset){$process=Start-Process -FilePath $ue.Editor -ArgumentList $arguments -PassThru}
 else{$process=Start-Process -FilePath $ue.Editor -ArgumentList $arguments -WindowStyle Hidden -PassThru}
 [void]$process.Handle;Write-Output "IMU server PID $($process.Id)"
 $client=@((Get-Process -Id $PID).Path,'-NoProfile','-File',(Join-Path $root 'tools/integration/Run-ImuProbe.ps1'),
   '-Port',$Port,'-OutputDirectory',(Join-Path $out 'ros'),'-Fixture',$Fixture)
 if($InteractiveReset){$client+='-WaitReset'}
 & (Join-Path $root 'tools/environment/run-env.ps1') -Command $client
 if($LASTEXITCODE -ne 0){throw 'Native IMU gate failed.'}
}finally{if($process -and -not $process.HasExited){Stop-Process -Id $process.Id -Force}}
