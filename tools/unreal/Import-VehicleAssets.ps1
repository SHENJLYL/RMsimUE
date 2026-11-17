param(
    [string]$EngineRoot = '',
    [Parameter(Mandatory = $true)][string]$Manifest,
    [switch]$ContactCollision,
    [int]$TimeoutSeconds = 300
)
. (Join-Path $PSScriptRoot 'Common.ps1')
try {
    $ue = Get-UnrealTools -EngineRoot $EngineRoot
    $manifestPath = (Resolve-Path -LiteralPath $Manifest).Path
    $script = Join-Path $PSScriptRoot 'import_vehicle_assets.py'
    $arguments = @(
        (Quote-UnrealArgument $ue.Project), '-run=pythonscript',
        ('-script=' + (Quote-UnrealArgument $script)),
        ('-AssetManifest=' + (Quote-UnrealArgument $manifestPath)),
        '-unattended', '-nop4', '-NoSplash'
    )
    $process = Start-Process -FilePath $ue.EditorCmd -ArgumentList $arguments -WindowStyle Hidden -PassThru
    [void]$process.Handle
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        Stop-Process -Id $process.Id -Force
        throw 'Asset import timed out. Inspect RMSim/Saved/Logs before retrying.'
    }
    $process.WaitForExit();$process.Refresh()
    if ($null -eq $process.ExitCode) { throw 'Import exit code is unavailable.' }
    if ($process.ExitCode -ne 0) { exit $process.ExitCode }
    if ($ContactCollision) {
        $rig=Join-Path (Split-Path -Parent $manifestPath) 'ue_mesh_rig.json'
        $evidence=Join-Path $PSScriptRoot ('../../artifacts/assets/contact-physics/prepare-'+[DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fffffff'))
        $arguments=@((Quote-UnrealArgument $ue.Project),'-run=pythonscript',
            ('-script='+(Quote-UnrealArgument (Join-Path $PSScriptRoot 'ensure_vehicle_collision.py'))),
            ('-CollisionRig='+(Quote-UnrealArgument $rig)),('-CollisionEvidence='+(Quote-UnrealArgument ([IO.Path]::GetFullPath($evidence)))),
            '-unattended','-nop4','-NoSplash')
        $process=Start-Process -FilePath $ue.EditorCmd -ArgumentList $arguments -WindowStyle Hidden -PassThru
        [void]$process.Handle
        if (-not $process.WaitForExit($TimeoutSeconds*1000)) { Stop-Process -Id $process.Id -Force;throw 'Contact collision preparation timed out.' }
        $process.WaitForExit();$process.Refresh()
        if ($null -eq $process.ExitCode) { throw 'Collision preparation exit code is unavailable.' }
        exit $process.ExitCode
    }
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 3
}
