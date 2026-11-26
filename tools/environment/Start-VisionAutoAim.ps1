param(
    [string]$RobotId = 'aim_sentry',
    [int]$Domain = 71,
    [double]$MuzzleSpeedMps = 24.5,
    [switch]$RequireBehaviorSwitch
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$env:ROS_DOMAIN_ID = "$Domain"
$env:PIXI_CACHE_DIR = 'E:\simulator-cache\pixi'
$env:PIXI_HOME = 'E:\simulator-cache\pixi-home'
$env:PIXI_NO_CONFIG = '1'

$pyPaths = @(
    (Join-Path $root 'ros2_ws/src/sp_vision_25'),
    (Join-Path $root 'ros2_ws/src/rmsim_bridge'),
    (Join-Path $root 'ros2_ws/install_bridge/rmsim_bridge/Lib/site-packages'),
    (Join-Path $root 'ros2_ws/install_control/sp_msgs/Lib/site-packages'),
    (Join-Path $root 'ros2_ws/install_control/pb_rm_interfaces/Lib/site-packages')
)
$amentPaths = @(
    (Join-Path $root 'ros2_ws/install_control/sp_msgs'),
    (Join-Path $root 'ros2_ws/install_control/pb_rm_interfaces'),
    (Join-Path $root 'ros2_ws/install_bridge/rmsim_bridge')
)
$binPaths = @(
    (Join-Path $root 'ros2_ws/install_control/sp_msgs/bin'),
    (Join-Path $root 'ros2_ws/install_control/pb_rm_interfaces/bin')
)
$env:PATH = ($binPaths -join ';') + ';' + $env:PATH
$env:AMENT_PREFIX_PATH = ($amentPaths -join ';') + $(if ($env:AMENT_PREFIX_PATH) { ';' + $env:AMENT_PREFIX_PATH } else { '' })
$env:PYTHONPATH = ($pyPaths -join ';') + $(if ($env:PYTHONPATH) { ';' + $env:PYTHONPATH } else { '' })

$reqBt = if ($RequireBehaviorSwitch) { 'true' } else { 'false' }
Write-Host "[sp_vision_25] Starting OpenVINO YOLOv8 + tiny_resnet + PnP + Kalman + Ballistic Auto-Aim (robot_id=$RobotId, domain=$Domain, require_bt=$reqBt)..."
& 'E:\simulator-tools\pixi\pixi.exe' run --manifest-path (Join-Path $PSScriptRoot 'pixi.toml') `
    python -c "from sp_vision_25.vision_node import main; main()" `
    --ros-args -p "robot_id:=$RobotId" -p "muzzle_speed_mps:=$MuzzleSpeedMps" -p "require_behavior_switch:=$reqBt"
exit $LASTEXITCODE
