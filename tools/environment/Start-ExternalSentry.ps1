param(
    [string]$RobotId = 'aim_sentry',
    [int]$Domain = 71,
    [string]$NavigationSource = '',
    [string]$MapYaml = '',
    [string]$LocalizationMapYaml = '',
    [string]$DriveProfile = '',
    [switch]$NoRviz,
    [switch]$CheckContractOnly,
    [switch]$MatchAutonomy,
    [switch]$NoBehavior,
    [switch]$NoVision,
    [switch]$TruthVision,
    [string]$MapFile = '',
    [ValidatePattern('^[a-z][a-z0-9_]*$')][string]$SpawnId = 'default'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
if (-not $NavigationSource) {
    $NavigationSource = Join-Path $root 'ros2_ws/src/pb2025_nav_bringup/config/simulation/nav2_params.yaml'
}
$NavigationSource = [IO.Path]::GetFullPath($NavigationSource)
if (-not (Test-Path -LiteralPath $NavigationSource -PathType Leaf)) {
    throw "External sentry_ws nav2_params.yaml not found: $NavigationSource"
}
if (-not $MapFile) {
    $MapFile = Join-Path $root 'assets/maps/rmuc2027/map.json'
}
if (-not $DriveProfile) {
    $DriveProfile = Join-Path $root 'assets/vehicles/aim_sentry/drive-profile.json'
}

Write-Output "=== External sentry_ws Connector (Decoupled from UE5 Process) ==="
Write-Output "RobotId          : $RobotId"
Write-Output "ROS_DOMAIN_ID    : $Domain"
Write-Output "NavigationSource : $NavigationSource"
Write-Output "Simulator map    : $MapFile (spawn=$SpawnId)"
Write-Output ('Map YAML         : ' + $(if ($MapYaml) { $MapYaml } else { 'RMUC package snapshot' }))
Write-Output ('Loc Map YAML     : ' + $(if ($LocalizationMapYaml) { $LocalizationMapYaml } elseif ($MapYaml) { 'From selected planning map' } else { 'RMUC package default' }))
Write-Output "Contract Topics  :"
Write-Output "  Clock          : /clock (use_sim_time:=true)"
Write-Output "  Chassis Cmd    : /$RobotId/cmd_vel (geometry_msgs/TwistStamped, frame_id=$RobotId/base_link)"
Write-Output "  LiDAR          : /$RobotId/lidar/livox (livox_ros_driver2/CustomMsg) & /$RobotId/lidar/points (PointCloud2)"
Write-Output "  IMU            : /$RobotId/imu/data_raw (sensor_msgs/Imu)"
Write-Output "  Camera         : /$RobotId/camera/image_raw & /$RobotId/camera/camera_info"
Write-Output "  Gimbal State   : /$RobotId/gimbal/joint_states (sensor_msgs/JointState)"
Write-Output "  TF             : /tf ($RobotId/base_link -> sensors & yaw_link)"

if ($CheckContractOnly) {
    exit 0
}

$setupBat = "call `"$root\ros2_ws\install_lio\setup.bat`" && call `"$root\ros2_ws\install_navigation\setup.bat`" && call `"$root\ros2_ws\install_bridge\setup.bat`""
if ($MatchAutonomy) {
    $controlSetup = Join-Path $root 'ros2_ws/install_control/local_setup.bat'
    if (!(Test-Path -LiteralPath $controlSetup -PathType Leaf)) { throw "Missing match messages: $controlSetup" }
    $setupBat += " && call `"$controlSetup`""
    if (-not $NoBehavior) {
        $behaviorSetup = Join-Path $root 'ros2_ws/install_behavior/local_setup.bat'
        if (!(Test-Path -LiteralPath $behaviorSetup -PathType Leaf)) { throw 'Run tools/environment/Build-Behavior.ps1 before starting match autonomy.' }
        $setupBat += " && call `"$behaviorSetup`""
    }
}
$connector = Join-Path $root 'tools\integration\external_sentry_connector.py'
$noRvizArg = if ($NoRviz) { ' --no-rviz' } else { '' }
$mapArg = if ($MapYaml) { " --map-yaml `"$MapYaml`"" } else { '' }
$localizationArg = if ($LocalizationMapYaml) { " --localization-map-yaml `"$LocalizationMapYaml`"" } else { '' }
$matchArg = if ($MatchAutonomy) { ' --match-autonomy' } else { '' }
$noBehaviorArg = if ($NoBehavior) { ' --no-behavior' } else { '' }
$noVisionArg = if ($NoVision) { ' --no-vision' } else { '' }
$visionBackendArg = if ($TruthVision) { ' --vision-backend truth' } else { '' }
$cmdLine = "$setupBat && python `"$connector`" --robot-id $RobotId --domain $Domain --navigation-source `"$NavigationSource`" --map-file `"$MapFile`" --spawn-id $SpawnId --drive-profile `"$DriveProfile`"$mapArg$localizationArg$noRvizArg$matchArg$noBehaviorArg$noVisionArg$visionBackendArg"

& (Join-Path $PSScriptRoot 'run-env.ps1') cmd /c $cmdLine
exit $LASTEXITCODE
