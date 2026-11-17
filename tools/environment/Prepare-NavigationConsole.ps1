$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
& (Join-Path $PSScriptRoot 'run-env.ps1') -Command @('python',(Join-Path $PSScriptRoot 'stage_rviz_runtime.py'))
exit $LASTEXITCODE
