param([int]$Port=9451,[switch]$MotorControl,[switch]$AimControl,[switch]$Lidar)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$setup=Join-Path $root 'ros2_ws/install_bridge/local_setup.ps1'
if(-not(Test-Path -LiteralPath $setup)){throw 'Build rmsim_bridge into ros2_ws/install_bridge first.'}
if($MotorControl -or $AimControl){. (Join-Path $root 'ros2_ws/install_control/local_setup.ps1')}
if($Lidar){. (Join-Path $root 'ros2_ws/install_lio/local_setup.ps1')}
. $setup
$backend=if($MotorControl){'lk'}else{'velocity'}
$aim=if($AimControl){'true'}else{'false'}
& ros2 run rmsim_bridge bridge --ros-args -p "port:=$Port" -p "control_backend:=$backend" -p "aim_control:=$aim"
exit $LASTEXITCODE
