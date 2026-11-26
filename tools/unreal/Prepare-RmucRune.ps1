param(
    [string]$EngineRoot = 'E:/simulator-tools/UE_5.8',
    [string]$Blender = 'E:/blender/blender.exe',
    [string]$Source = '',
    [string]$OutputDirectory = '',
    [switch]$DartOnly,
    [switch]$OutpostsOnly,
    [switch]$VisibilityOnly,
    [switch]$BaseArmorOnly,
    [switch]$RuneLightsOnly
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
if (([int]$DartOnly.IsPresent + [int]$OutpostsOnly.IsPresent + [int]$VisibilityOnly.IsPresent + [int]$BaseArmorOnly.IsPresent + [int]$RuneLightsOnly.IsPresent) -gt 1) { throw 'Select one device group per import.' }
if ($RuneLightsOnly) {
    $ue = Get-UnrealTools -EngineRoot $EngineRoot
    $script = Join-Path $PSScriptRoot 'import_rmuc_rune.py'
    $arguments = @((Quote-UnrealArgument $ue.Project), '-run=pythonscript',
        ('-script=' + (Quote-UnrealArgument $script)), '-RuneLightsOnly', '-unattended', '-nop4', '-NoSplash')
    $process = Start-Process -FilePath $ue.EditorCmd -ArgumentList $arguments -WindowStyle Hidden -PassThru
    [void]$process.Handle
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw "Rune light material import failed ($($process.ExitCode)). Check Unreal Editor logs." }
    Write-Output 'Generated the RMUC rotor LED material.'
    return
}
$device = if ($VisibilityOnly) { 'visibility' } elseif ($DartOnly) { 'dart' } elseif ($OutpostsOnly) { 'outpost' } elseif ($BaseArmorOnly) { 'base-armor' } else { 'rune' }
if (!$Source) {
    $sourceName = if ($VisibilityOnly) { 'full-map-collision.glb' } else { 'full-map.glb' }
    $Source = Join-Path $root "artifacts/sources/rmuc2027/optimized/rmuc2027/$sourceName"
}
if (!$OutputDirectory) {
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fffffff')
    $OutputDirectory = Join-Path $root "artifacts/maps/rmuc2027/$device-$stamp"
}
$Source = (Resolve-Path -LiteralPath $Source).Path
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$prepare = Join-Path $root 'tools/maps/prepare_rmuc_rune.py'
$deviceArgs = @()
if ($DartOnly) { $deviceArgs += '--dart-only' }
if ($OutpostsOnly) { $deviceArgs += '--outposts-only' }
if ($VisibilityOnly) { $deviceArgs += '--visibility-only' }
if ($BaseArmorOnly) { $deviceArgs += @('--base-armor-only', '--kinematics', (Join-Path $root 'assets/maps/rmuc2027/base-armor-kinematics.json')) }
& $Blender --background --factory-startup --disable-autoexec --python-exit-code 1 --python $prepare -- --source $Source --output $OutputDirectory @deviceArgs
if ($LASTEXITCODE -ne 0) { throw "Field device source preparation failed ($LASTEXITCODE)." }

$ue = Get-UnrealTools -EngineRoot $EngineRoot
$reportParameter = if ($VisibilityOnly) { '-VisibilitySource=' } elseif ($DartOnly) { '-DartSource=' } elseif ($OutpostsOnly) { '-OutpostSource=' } elseif ($BaseArmorOnly) { '-BaseArmorSource=' } else { '-RuneSource=' }
$report = Join-Path $OutputDirectory "$device-source.json"
$script = Join-Path $PSScriptRoot 'import_rmuc_rune.py'
$arguments = @(
    (Quote-UnrealArgument $ue.Project), '-run=pythonscript', ('-script=' + (Quote-UnrealArgument $script)),
    ($reportParameter + (Quote-UnrealArgument $report)), '-unattended', '-nop4', '-NoSplash'
)
$process = Start-Process -FilePath $ue.EditorCmd -ArgumentList $arguments -WindowStyle Hidden -PassThru
[void]$process.Handle
$process.WaitForExit()
if ($process.ExitCode -ne 0) { throw "Field device UE asset import failed ($($process.ExitCode)). Check Unreal Editor logs." }
Write-Output "Derived field device source report: $report"
