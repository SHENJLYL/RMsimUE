param([Parameter(Mandatory=$true)][string]$MapYaml,[string]$RobotId='aim_sentry',[double]$InitialX=.6,[double]$InitialY=-.4,[double]$InitialYaw=.15)
$ErrorActionPreference='Stop';$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'));$culture=[Globalization.CultureInfo]::InvariantCulture
. (Join-Path $root 'ros2_ws/install_lio/local_setup.ps1')
. (Join-Path $root 'ros2_ws/install_navigation/local_setup.ps1')
. (Join-Path $root 'ros2_ws/install_bridge/local_setup.ps1')
$pose='['+$InitialX.ToString($culture)+','+$InitialY.ToString($culture)+','+$InitialYaw.ToString($culture)+']'
& ros2 run rmsim_bridge navigation --ros-args -p ('robot_id:='+$RobotId) -p ('initial_pose:='+$pose) -p ('map_yaml:='+[IO.Path]::GetFullPath($MapYaml)) `
 -p ('reference_config:='+(Join-Path $root 'ros2_ws/src/pb2025_nav_bringup/config/reality/nav2_params.yaml')) `
 -p ('native_runtime:='+(Join-Path $root 'artifacts/runtime/navigation/windows')) -p ('output_directory:='+(Join-Path $root 'artifacts/navigation/live'))
exit $LASTEXITCODE
