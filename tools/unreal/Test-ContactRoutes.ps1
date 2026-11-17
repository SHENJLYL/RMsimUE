param(
    [Parameter(Mandatory=$true)][string]$EngineRoot,
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
    [double]$SpeedMps=1,
    [int]$TimeoutSeconds=240
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$evidence=[IO.Path]::GetFullPath($EvidenceDirectory)
if(Test-Path -LiteralPath $evidence){throw 'Use a fresh route evidence directory.'}
New-Item -ItemType Directory -Path $evidence | Out-Null
$source=Join-Path $root 'assets/maps/rmuc2027/map.json'
$map=Get-Content -LiteralPath $source -Raw -Encoding UTF8 | ConvertFrom-Json
$map.spawns=@(
    @{id='wave_red';team='red';position_m=@(5.8,6.4,0.03147864);yaw_rad=0},
    @{id='wave_blue';team='blue';position_m=@(-5.8,-6.4,0.03147888);yaw_rad=[Math]::PI}
)
$mapPath=Join-Path $evidence 'wave-map.json'
$map | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $mapPath -Encoding utf8
$shell=(Get-Process -Id $PID).Path
foreach($spawn in @('wave_red','wave_blue')){
    & $shell -NoProfile -File (Join-Path $PSScriptRoot 'Test-ManualControls.ps1') -EngineRoot $EngineRoot `
        -Contact -ContactDistanceCm 340 -ContactSpeedMps $SpeedMps -MapFile $mapPath -SpawnId $spawn `
        -EvidenceDirectory (Join-Path $evidence $spawn) -TimeoutSeconds $TimeoutSeconds
    if($LASTEXITCODE -ne 0){throw "Native contact route failed at $spawn; preserved actual evidence."}
}
@{status='passed';routes=@('wave_red','wave_blue');distance_cm=340;command_speed_m_s=$SpeedMps;scope='actual native contact vehicle on both source wave sections'} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'verification.json') -Encoding utf8
