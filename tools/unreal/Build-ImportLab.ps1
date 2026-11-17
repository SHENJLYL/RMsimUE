param([string]$EngineRoot = '')
. (Join-Path $PSScriptRoot 'Common.ps1')
try {
    $ue = Get-UnrealTools -EngineRoot $EngineRoot
    # Build.bat enables delayed expansion; a sandbox TEMP containing ! breaks its lock path.
    $buildTemp = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../artifacts/unreal/build-temp'))
    New-Item -ItemType Directory -Force -Path $buildTemp | Out-Null
    $env:TEMP = $buildTemp
    $env:TMP = $buildTemp
    $buildLog = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../artifacts/unreal/build.log'))
    & $ue.Build RMSimEditor Win64 Development $ue.Project -WaitMutex -NoHotReloadFromIDE -NoUBA -MaxParallelActions=8 "-Log=$buildLog"
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 3
}
