param(
    [Parameter(Mandatory = $true)][string]$EngineRoot,
    [Parameter(Mandatory = $true)][string]$SourceFile,
    [int]$TimeoutSeconds = 600
)
. (Join-Path $PSScriptRoot 'Common.ps1')
try {
    $ue = Get-UnrealTools -EngineRoot $EngineRoot
    $source = (Resolve-Path -LiteralPath $SourceFile).Path
    $importer = Join-Path $PSScriptRoot 'import_mesh_map.py'
    if ($TimeoutSeconds -lt 1) { throw 'TimeoutSeconds must be positive.' }
    $arguments = @((Quote-UnrealArgument $ue.Project), '-run=pythonscript',
        ('-script=' + (Quote-UnrealArgument $importer)),
        ('-MapSource=' + (Quote-UnrealArgument $source)), '-unattended', '-nop4', '-NoSplash')
    $process = Start-Process -FilePath $ue.EditorCmd -ArgumentList $arguments -WindowStyle Hidden -PassThru
    [void]$process.Handle
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        Stop-Process -Id $process.Id -Force
        throw 'Map import timed out; runtime acceptance is not inferred.'
    }
    $process.WaitForExit(); $process.Refresh()
    if ($null -eq $process.ExitCode -or $process.ExitCode -ne 0) { throw "Map import failed: $($process.ExitCode)" }
    Write-Output "Imported map assets from $source. Runtime driving/visual evidence is a separate check."
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 3
}
