param(
    [Parameter(Mandatory = $true)][string]$EngineRoot,
    [string]$Manifest = '',
    [string]$MapFile = '',
    [string]$SpawnId = 'default',
    [switch]$TrainingMode,
    [switch]$KinematicPreview,
    [switch]$RosBridge,
    [switch]$RosControl,
    [switch]$RosMotorControl,
    [switch]$RosAimControl,
    [switch]$RosImu,
    [string]$ImuProfile='',
    [switch]$RosLidar,
    [string]$LidarProfile='',
    [double]$SimStepSeconds=0,
    [int]$RosPort=9451,
    [switch]$CaptureOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
if ([string]::IsNullOrWhiteSpace($Manifest)) {
    $Manifest = Join-Path $repo 'assets/vehicles/aim_sentry/asset_import.json'
}
if ([string]::IsNullOrWhiteSpace($MapFile)) { $MapFile = Join-Path $repo 'assets/maps/rmuc2027/map.json' }
$manifestPath = (Resolve-Path -LiteralPath $Manifest).Path
$shellPath = (Get-Process -Id $PID).Path
$evidence = Join-Path $repo 'artifacts/unreal'
New-Item -ItemType Directory -Force -Path $evidence | Out-Null
$record = [ordered]@{
    utc = [DateTime]::UtcNow.ToString('o')
    manifest = $manifestPath
    engine = $EngineRoot
    status = 'started'
    runtime_verified = $false
    steps = @()
}

function Invoke-Step {
    param([string]$Name, [string[]]$Arguments)
    & $shellPath -NoProfile -File (Join-Path $PSScriptRoot $Name) @Arguments
    $code = $LASTEXITCODE
    $record.steps += @{ script = $Name; exit_code = $code }
    if ($code -ne 0) { throw "$Name failed with exit code $code" }
}

try {
    Invoke-Step 'Check-Engine.ps1' @('-EngineRoot', $EngineRoot)
    Invoke-Step 'Build-ImportLab.ps1' @('-EngineRoot', $EngineRoot)
    $importArgs=@('-EngineRoot', $EngineRoot, '-Manifest', $manifestPath)
    if (-not $KinematicPreview) { $importArgs += '-ContactCollision' }
    Invoke-Step 'Import-VehicleAssets.ps1' $importArgs
    $mapSource = Join-Path (Split-Path -Parent ([IO.Path]::GetFullPath($MapFile))) 'mesh-source.json'
    if (Test-Path -LiteralPath $mapSource) { Invoke-Step 'Import-MeshMap.ps1' @('-EngineRoot', $EngineRoot, '-SourceFile', $mapSource) }
    $rig = Join-Path (Split-Path -Parent $manifestPath) 'ue_mesh_rig.json'
    if (-not (Test-Path -LiteralPath $rig -PathType Leaf)) { throw "Importer did not create $rig" }
    $runArgs = @('-EngineRoot', $EngineRoot, '-InputType', 'MeshRig', '-InputFile', $rig)
    if ($MapFile) { $runArgs += @('-MapFile', [IO.Path]::GetFullPath($MapFile)) }
    $runArgs += @('-SpawnId', $SpawnId)
    if (-not $KinematicPreview) { $runArgs += '-ContactPhysics' }
    if ($TrainingMode) { $runArgs += '-TrainingMode' }
    if ($RosBridge -or $RosControl -or $RosMotorControl -or $RosAimControl -or $RosImu -or $ImuProfile -or $RosLidar -or $LidarProfile) { $runArgs += @('-RosBridge','-RosPort',$RosPort) }
    if ($RosControl) { $runArgs += '-RosControl' }
    if ($RosMotorControl) { $runArgs += '-RosMotorControl' }
    if ($RosAimControl) { $runArgs += '-RosAimControl' }
    if ($RosImu -or $ImuProfile) { $runArgs += '-RosImu' }
    if ($ImuProfile) { $runArgs += @('-ImuProfile',$ImuProfile) }
    if ($RosLidar -or $LidarProfile) { $runArgs += '-RosLidar' }
    if ($LidarProfile) { $runArgs += @('-LidarProfile',$LidarProfile) }
    if ($SimStepSeconds -ne 0) { $runArgs += @('-SimStepSeconds',$SimStepSeconds.ToString([Globalization.CultureInfo]::InvariantCulture)) }
    if ($CaptureOnly) {
        $capture = Join-Path $evidence ('capture-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'))
        $runArgs += @('-CaptureDirectory', $capture, '-ExitAfterCapture')
    }
    Invoke-Step 'Run-ImportLab.ps1' $runArgs
    $record.status = if ($CaptureOnly) { 'capture_process_exited' } else { 'interactive_launch_requested' }
    $record.note = 'Process exit/start is not visual or manual-control acceptance. Inspect captures and test driving.'
} catch {
    $record.status = 'failed'
    $record.error = $_.Exception.Message
    [Console]::Error.WriteLine($record.error)
} finally {
    $record | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 -LiteralPath (Join-Path $evidence 'last-launch.json')
}
if ($record.status -eq 'failed') { exit 3 }
