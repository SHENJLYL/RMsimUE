param([string]$EngineRoot = '')
. (Join-Path $PSScriptRoot 'Common.ps1')
try {
    $ue = Get-UnrealTools -EngineRoot $EngineRoot
    [pscustomobject]@{
        status = 'engine_files_found'
        engine = $ue.EngineRoot
        project = $ue.Project
        compiled = $false
        runtime_verified = $false
        note = 'File presence only. C++ toolchain, modules and rendering still require build/run.'
    } | ConvertTo-Json
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 3
}
