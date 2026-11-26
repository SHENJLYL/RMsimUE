param([int]$Port=9451,[switch]$MotorControl,[switch]$AimControl,[switch]$Lidar,
      [ValidatePattern('^[A-Za-z][A-Za-z0-9_]{0,63}$')][string]$RobotId='aim_sentry',
      [ValidateRange(0,232)][int]$Domain,[string]$NavigationFollowProfile='')
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$setup=Join-Path $root 'ros2_ws/install_bridge/local_setup.ps1'
if(-not(Test-Path -LiteralPath $setup)){throw 'Build rmsim_bridge into ros2_ws/install_bridge first.'}
if($MotorControl -or $AimControl){. (Join-Path $root 'ros2_ws/install_control/local_setup.ps1')}
if($Lidar){. (Join-Path $root 'ros2_ws/install_lio/local_setup.ps1')}
if($AimControl){
    $refereePrefix = Join-Path $root 'ros2_ws/install_behavior/dji_referee_protocol'
    $refereePython = Join-Path $refereePrefix 'Lib/site-packages'
    if(Test-Path -LiteralPath $refereePython -PathType Container){
        # This upstream ament package provides batch and DSV hooks only.
        $env:AMENT_PREFIX_PATH = "$refereePrefix;$env:AMENT_PREFIX_PATH"
        $env:PATH = "$(Join-Path $refereePrefix 'bin');$env:PATH"
        $env:PYTHONPATH = "$refereePython;$env:PYTHONPATH"
    }
}
. $setup
$backend=if($MotorControl){'lk'}else{'velocity'}
$aim=if($AimControl){'true'}else{'false'}
$rosArgs = @('--ros-args','-p',"port:=$Port",'-p',"robot_id:=$RobotId",'-p',"control_backend:=$backend",'-p',"aim_control:=$aim")
if($NavigationFollowProfile){
    if(-not $AimControl -or -not (Test-Path -LiteralPath $NavigationFollowProfile -PathType Leaf)){
        throw 'Navigation following requires aim control and a vehicle profile.'
    }
    $rosArgs+=@('-p','navigation_follow:=true','-p',"navigation_follow_profile:=$NavigationFollowProfile")
}
if ($PSBoundParameters.ContainsKey('Domain')) {
    $env:ROS_DOMAIN_ID = [string]$Domain
    # Match the external navigation processes' DDS transport before rclpy initializes.
    & python -c 'from rmsim_bridge.native_transport import configure_navigation_transport; configure_navigation_transport(); from rmsim_bridge.node import main; main()' @rosArgs
} else {
    & ros2 run rmsim_bridge bridge @rosArgs
}
exit $LASTEXITCODE
