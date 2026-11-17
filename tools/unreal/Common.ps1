Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-UnrealTools {
    param([string]$EngineRoot)
    if ([string]::IsNullOrWhiteSpace($EngineRoot)) {
        throw 'EngineRoot is required: pass the installed UE directory explicitly. No download is attempted.'
    }
    $engine = (Resolve-Path -LiteralPath $EngineRoot -ErrorAction Stop).Path
    $project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../unreal/RMSim/RMSim.uproject'))
    $files = @{
        EngineRoot = $engine
        Project = $project
        Build = Join-Path $engine 'Engine/Build/BatchFiles/Build.bat'
        Editor = Join-Path $engine 'Engine/Binaries/Win64/UnrealEditor.exe'
        EditorCmd = Join-Path $engine 'Engine/Binaries/Win64/UnrealEditor-Cmd.exe'
    }
    foreach ($key in @('Project', 'Build', 'Editor', 'EditorCmd')) {
        if (-not (Test-Path -LiteralPath $files[$key] -PathType Leaf)) {
            throw "Missing $key at $($files[$key]). Install/build UE first; this script does not download or install it."
        }
    }
    return [pscustomobject]$files
}

function Quote-UnrealArgument {
    param([string]$Value)
    if ($Value.Contains('"') -or $Value.Contains("`n") -or $Value.Contains("`r")) {
        throw 'Unsupported quote/newline in argument.'
    }
    # UE accepts forward slashes; avoid Windows quoted trailing-backslash ambiguity.
    return '"' + $Value.Replace('\', '/') + '"'
}

function Assert-ManualProbeEvidence {
    param(
        [Parameter(Mandatory = $true)][string]$EvidenceDirectory,
        [AllowNull()][object]$ProcessExitCode,
        [DateTime]$RunStartedUtc = [DateTime]::MinValue
    )
    $root = [IO.Path]::GetFullPath($EvidenceDirectory)
    $reportPath = Join-Path $root 'probe-result.json'
    if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) { throw "Probe result missing: $reportPath" }
    $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    if ($report.status -ne 'passed' -or $report.complete -ne $true -or $report.kinematic_probe_passed -ne $true) {
        throw "Probe did not pass (status=$($report.status)); inspect $reportPath"
    }
    if ($ProcessExitCode -isnot [int] -or $ProcessExitCode -ne 0) {
        throw "Probe process exit was not a verified zero: $ProcessExitCode"
    }
    $reportedStart = if ($report.started_utc -is [DateTime]) {
        $report.started_utc.ToUniversalTime()
    } else { [DateTimeOffset]::Parse($report.started_utc).UtcDateTime }
    if ($RunStartedUtc -ne [DateTime]::MinValue -and
        $reportedStart -lt $RunStartedUtc.AddSeconds(-1)) {
        throw 'Probe result predates this process; stale evidence is not accepted.'
    }
    $expected = @('initial_state', 'align_with_obstacle', 'free_translation', 'long_sweep_blocks',
        'blocked_motion_does_not_spin_wheels', 'rotation_blocked_near_wall', 'reverse_away_from_wall',
        'free_rotation', 'gimbal_and_camera_transform')
    if (@($report.checks).Count -ne $expected.Count) { throw 'Probe result does not contain all nine checks.' }
    foreach ($name in $expected) {
        $matches = @($report.checks | Where-Object { $_.name -eq $name })
        if ($matches.Count -ne 1 -or $matches[0].passed -ne $true) { throw "Probe check failed/missing/duplicated: $name" }
    }
    $initial = @($report.checks | Where-Object { $_.name -eq 'initial_state' })[0]
    $cameraNames = @('overview') + @($initial.before.camera_world_transforms.PSObject.Properties.Name)
    if ($cameraNames.Count -lt 2 -or @($report.captures).Count -ne 3 * $cameraNames.Count) {
        throw 'Probe capture set is incomplete.'
    }
    Add-Type -AssemblyName System.Drawing
    foreach ($phase in @('initial', 'blocked', 'gimbal')) {
        foreach ($camera in $cameraNames) {
            $captures = @($report.captures | Where-Object { $_.phase -eq $phase -and $_.camera -eq $camera })
            if ($captures.Count -ne 1) { throw "Missing/duplicated capture: $phase/$camera" }
            $capture = $captures[0]
            $path = [IO.Path]::GetFullPath($capture.path)
            $prefix = $root.TrimEnd([char[]]'\/') + [IO.Path]::DirectorySeparatorChar
            if (-not $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
                -not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Capture unavailable/outside evidence directory: $path" }
            $image = $null
            try {
                $image = [Drawing.Image]::FromFile($path)
                if ($image.RawFormat.Guid -ne [Drawing.Imaging.ImageFormat]::Png.Guid -or
                    $image.Width -ne $capture.width -or $image.Height -ne $capture.height) {
                    throw "Capture format/dimensions do not match runtime report: $path"
                }
            } finally { if ($null -ne $image) { $image.Dispose() } }
        }
    }
    return $report
}
