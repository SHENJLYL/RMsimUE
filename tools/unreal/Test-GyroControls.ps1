param(
    [Parameter(Mandatory=$true)][string]$EngineRoot,
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
    [switch]$WaveRoutes,
    [double]$SpeedMps=0.35,
    [double]$SpinRadS=2,
    [switch]$PrecisionKeys,
    [int]$TimeoutSeconds=240
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$evidence=[IO.Path]::GetFullPath($EvidenceDirectory)
if(Test-Path -LiteralPath $evidence){throw 'Use a fresh gyro evidence directory.'}
New-Item -ItemType Directory -Path $evidence | Out-Null
$source=if($WaveRoutes){'assets/maps/rmuc2027/map.json'}else{'assets/maps/import_lab/map.json'}
$map=Get-Content -LiteralPath (Join-Path $root $source) -Raw -Encoding UTF8 | ConvertFrom-Json
if($WaveRoutes){
    $map.spawns=@(
        @{id='wave_red';team='red';position_m=@(5.8,6.4,0.03147864);yaw_rad=0},
        @{id='wave_blue';team='blue';position_m=@(-5.8,-6.4,0.03147888);yaw_rad=[Math]::PI})
    $spawns=@('wave_red','wave_blue')
}else{
    $map.obstacles=@();$map.PSObject.Properties.Remove('inspection_obstacle_id')
    $spawns=@('default')
}
$mapPath=Join-Path $evidence 'map.json'
$map | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $mapPath -Encoding utf8
$shell=(Get-Process -Id $PID).Path
foreach($spawn in $spawns){
    $arguments=@('-EngineRoot',$EngineRoot,'-Gyro','-ContactSpeedMps',$SpeedMps,
        '-MapFile',$mapPath,'-SpawnId',$spawn,'-EvidenceDirectory',(Join-Path $evidence $spawn),'-TimeoutSeconds',$TimeoutSeconds)
    if($WaveRoutes){$arguments+=@('-GyroRoute','-GyroSpinRadS',$SpinRadS)}
    if($PrecisionKeys){if(-not $WaveRoutes){throw 'PrecisionKeys needs WaveRoutes.'};$arguments+='-PrecisionKeys'}
    & $shell -NoProfile -File (Join-Path $PSScriptRoot 'Test-ManualControls.ps1') @arguments
    if($LASTEXITCODE -ne 0){throw "Native gyro check failed at $spawn; evidence preserved."}
}
$firstReport=Get-Content -LiteralPath (Join-Path $evidence ($spawns[0]+'/control-result.json')) -Raw -Encoding UTF8 | ConvertFrom-Json
if($WaveRoutes){$SpeedMps=$firstReport.gyro_route_speed_m_s}
@{passed=$true;wave_routes=$WaveRoutes.IsPresent;spawns=$spawns;speed_m_s=$SpeedMps;spin_rad_s=$SpinRadS;precision_keys=$PrecisionKeys.IsPresent;scope='native physical camera-relative swerve controls'} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'verification.json') -Encoding utf8
