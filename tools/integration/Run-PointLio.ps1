param([string]$RobotId='aim_sentry',[string]$ImuId='imu')
$ErrorActionPreference='Stop';$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
. (Join-Path $root 'ros2_ws/install_lio/local_setup.ps1')
. (Join-Path $root 'ros2_ws/install_bridge/local_setup.ps1')
& ros2 run rmsim_bridge point_lio --ros-args -p "robot_id:=$RobotId" -p "imu_id:=$ImuId" -p ('output_directory:='+(Join-Path $root 'artifacts/localization/pointlio'))
exit $LASTEXITCODE
