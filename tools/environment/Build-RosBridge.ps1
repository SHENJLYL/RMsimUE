$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$setup=Join-Path $root 'ros2_ws/install_lio/local_setup.ps1'
if(-not(Test-Path -LiteralPath $setup)){throw 'Build native Livox interfaces first with Build-PointLio.cmd.'}
. $setup
Push-Location (Join-Path $root 'ros2_ws')
try {
    & colcon --log-base log_bridge build --base-paths src --packages-select rmsim_bridge --build-base build_bridge --install-base install_bridge
    exit $LASTEXITCODE
} finally { Pop-Location }
