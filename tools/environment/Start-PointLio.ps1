param([string]$RobotId='aim_sentry',[string]$ImuId='imu')
$ErrorActionPreference='Stop';$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
& (Join-Path $PSScriptRoot 'run-env.ps1') -Command @('powershell','-NoProfile','-File',(Join-Path $root 'tools/integration/Run-PointLio.ps1'),'-RobotId',$RobotId,'-ImuId',$ImuId)
exit $LASTEXITCODE
