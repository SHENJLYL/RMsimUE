param([int]$Port=9451,[switch]$MotorControl,[switch]$AimControl,[switch]$Lidar,
      [ValidatePattern('^[A-Za-z][A-Za-z0-9_]{0,63}$')][string]$RobotId='aim_sentry',
      [ValidateRange(0,232)][int]$Domain)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$setup=Join-Path $root 'ros2_ws/install_bridge/local_setup.ps1'
if(-not(Test-Path -LiteralPath $setup)){throw 'Build rmsim_bridge into ros2_ws/install_bridge first.'}
if($MotorControl -or $AimControl){. (Join-Path $root 'ros2_ws/install_control/local_setup.ps1')}
if($Lidar){. (Join-Path $root 'ros2_ws/install_lio/local_setup.ps1')}
. $setup
$backend=if($MotorControl){'lk'}else{'velocity'}
$aim=if($AimControl){'true'}else{'false'}
$rosArgs = @('--ros-args','-p',"port:=$Port",'-p',"robot_id:=$RobotId",'-p',"control_backend:=$backend",'-p',"aim_control:=$aim")
if ($PSBoundParameters.ContainsKey('Domain')) {
    $env:ROS_DOMAIN_ID = [string]$Domain
    # Match the external navigation processes' DDS transport before rclpy initializes.
    & python -c 'from rmsim_bridge.native_transport import configure_navigation_transport; from rmsim_bridge.node import main; configure_navigation_transport(); main()' @rosArgs
} else {
    & ros2 run rmsim_bridge bridge @rosArgs
}
exit $LASTEXITCODE
