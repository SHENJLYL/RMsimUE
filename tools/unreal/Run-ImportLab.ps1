param(
    [string]$EngineRoot = '',
    [Parameter(Mandatory = $true)][string]$InputFile,
    [ValidateSet('Canonical', 'MeshRig')][string]$InputType = 'Canonical',
    [string]$CaptureDirectory = '',
    [double]$GimbalYaw = 0,
    [double]$GimbalPitch = 0,
    [switch]$ExitAfterCapture,
    [ValidateSet('', 'basic')][string]$ManualProbe = '',
    [string]$EvidenceDirectory = '',
    [string]$InputEvidence = '',
    [string]$MapFile = '',
    [string]$SpawnId = 'default',
    [string]$MapReport = '',
    [switch]$MapOverview,
    [switch]$TrainingMode,
    [switch]$ContactPhysics,
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
    [int]$TimeoutSeconds = 90
)
. (Join-Path $PSScriptRoot 'Common.ps1')
try {
    $ue = Get-UnrealTools -EngineRoot $EngineRoot
    # Canonical primitive preview has no imported wheel supports yet; retain its flat lab.
    if (-not $MapFile -and $InputType -eq 'Canonical') { $MapFile = Join-Path $PSScriptRoot '../../assets/maps/import_lab/map.json' }
    $inputPath = (Resolve-Path -LiteralPath $InputFile).Path
    if (-not (Test-Path -LiteralPath (Join-Path (Split-Path $ue.Project) 'Binaries/Win64/UnrealEditor-RMSim.dll'))) {
        throw 'RMSim editor module is not built. Run Build-ImportLab.ps1 first.'
    }
    if ($ExitAfterCapture -and [string]::IsNullOrWhiteSpace($CaptureDirectory)) {
        throw 'ExitAfterCapture requires CaptureDirectory.'
    }
    if ($TimeoutSeconds -lt 1) { throw 'TimeoutSeconds must be positive.' }
    if ($ManualProbe) {
        if ([string]::IsNullOrWhiteSpace($EvidenceDirectory)) { throw 'ManualProbe requires EvidenceDirectory.' }
        if (-not $MapFile) { $MapFile = Join-Path $PSScriptRoot '../../assets/maps/import_lab/map.json' }
        $probeDirectory = [IO.Path]::GetFullPath($EvidenceDirectory)
        if (Test-Path -LiteralPath (Join-Path $probeDirectory 'probe-result.json')) {
            throw 'Use a fresh evidence directory; an existing probe-result.json must not be overwritten or reused.'
        }
    }
    $flag = if ($InputType -eq 'MeshRig') { '-MeshRigFile=' } else { '-VehicleFile=' }
    $culture = [Globalization.CultureInfo]::InvariantCulture
    $arguments = @(
        (Quote-UnrealArgument $ue.Project),
        '/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode',
        '-game', '-ResX=1600', '-ResY=1000', '-NoSplash',
        ($flag + (Quote-UnrealArgument $inputPath)),
        ('-GimbalYaw=' + $GimbalYaw.ToString($culture)),
        ('-GimbalPitch=' + $GimbalPitch.ToString($culture))
    )
    if ($ManualProbe) {
        $arguments += @('-RenderOffScreen', '-unattended', '-ManualProbe=basic',
            ('-EvidenceDir=' + (Quote-UnrealArgument $probeDirectory)))
    } else { $arguments += '-windowed' }
    if ($CaptureDirectory) {
        $capture = [IO.Path]::GetFullPath($CaptureDirectory)
        $arguments += '-CaptureDir=' + (Quote-UnrealArgument $capture)
    }
    if ($ExitAfterCapture) { $arguments += '-ExitAfterCapture' }
    if ($InputEvidence) {
        $arguments += '-InputEvidence=' + (Quote-UnrealArgument ([IO.Path]::GetFullPath($InputEvidence)))
    }
    if ($MapFile) { $arguments += '-MapFile=' + (Quote-UnrealArgument (Resolve-Path -LiteralPath $MapFile).Path) }
    $arguments += '-SpawnId=' + (Quote-UnrealArgument $SpawnId)
    if ($MapReport) { $arguments += '-MapReport=' + (Quote-UnrealArgument ([IO.Path]::GetFullPath($MapReport))) }
    if ($MapOverview) { $arguments += '-MapOverview' }
    if ($TrainingMode) { $arguments += '-TrainingMode' }
    if ($RosBridge -or $RosControl -or $RosMotorControl -or $RosAimControl -or $RosImu -or $ImuProfile -or $RosLidar -or $LidarProfile) { $arguments += @('-RosBridge',('-RosPort='+$RosPort)) }
    if ($RosControl) { $arguments += '-RosControl' }
    if ($RosMotorControl) { $arguments += '-RosMotorControl' }
    if ($RosAimControl) { $arguments += '-RosAimControl' }
    if ($RosImu -or $ImuProfile) { $arguments += '-RosImu' }
    if ($ImuProfile) { $arguments += '-ImuProfile='+(Quote-UnrealArgument (Resolve-Path -LiteralPath $ImuProfile).Path) }
    if ($RosLidar -or $LidarProfile) { $arguments += '-RosLidar' }
    if ($LidarProfile) { $arguments += '-LidarProfile='+(Quote-UnrealArgument (Resolve-Path -LiteralPath $LidarProfile).Path) }
    if ($SimStepSeconds -ne 0) { $arguments += '-SimStepSeconds='+$SimStepSeconds.ToString($culture) }
    if ($ContactPhysics) { $arguments += '-ContactPhysics' }
    $runStartedUtc = [DateTime]::UtcNow
    if ($ManualProbe) {
        $process = Start-Process -FilePath $ue.Editor -ArgumentList $arguments -WindowStyle Hidden -PassThru
    } else {
        # A visible interactive application is the requested result.
        $process = Start-Process -FilePath $ue.Editor -ArgumentList $arguments -PassThru
    }
    # Keep a handle before exit so PowerShell's Process object can retain the real exit status.
    [void]$process.Handle
    if ($ExitAfterCapture -or $ManualProbe) {
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            Stop-Process -Id $process.Id -Force
            throw 'Capture run timed out; no success is inferred. Inspect project Saved/Logs.'
        }
        $process.WaitForExit()
        $process.Refresh()
        $processExitCode = $process.ExitCode
        if ($ManualProbe) {
            $result = Assert-ManualProbeEvidence -EvidenceDirectory $probeDirectory -ProcessExitCode $processExitCode -RunStartedUtc $runStartedUtc
            Write-Output "Kinematic probe passed with decoded capture evidence: $($result.run_id). Keyboard/appearance/dynamics remain separate checks."
        }
        if ($null -eq $processExitCode) { throw 'Process exit code is unavailable; it is not treated as zero.' }
        exit ([int]$processExitCode)
    }
    Write-Output "Started interactive import lab PID $($process.Id). This is launch acknowledgement, not runtime acceptance."
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 3
}
