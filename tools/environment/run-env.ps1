param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Command)
$ErrorActionPreference = 'Stop'

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$portableEnv = Join-Path $root 'runtime/ros2_env'
if (Test-Path -LiteralPath (Join-Path $portableEnv 'python.exe') -PathType Leaf) {
    $env:CONDA_PREFIX = $portableEnv
    $env:PYTHONHOME = ''
    $env:PYTHONNOUSERSITE = '1'
    $env:ROS_DISTRO = 'humble'
    $env:RMW_IMPLEMENTATION = 'rmw_fastrtps_cpp'
    if (-not $env:ROS_DOMAIN_ID) { $env:ROS_DOMAIN_ID = '71' }
    $env:ROS_LOCALHOST_ONLY = '1'
    $env:ROS_OS_OVERRIDE = 'conda:win64'
    $env:ROS_ETC_DIR = Join-Path $portableEnv 'Library/etc/ros'
    $libPrefix = Join-Path $portableEnv 'Library'
    $env:AMENT_PREFIX_PATH = if ($env:AMENT_PREFIX_PATH) { "$libPrefix;$env:AMENT_PREFIX_PATH" } else { $libPrefix }
    $env:AMENT_PYTHON_EXECUTABLE = Join-Path $portableEnv 'python.exe'
    $env:QT_PLUGIN_PATH = Join-Path $portableEnv 'Library/plugins'
    $env:PROJ_DATA = Join-Path $portableEnv 'Library/share/proj'
    $env:PROJ_LIB = Join-Path $portableEnv 'Library/share/proj'
    $rosLogs = Join-Path $root 'artifacts/ros-logs'
    $tmpDir = Join-Path $root 'artifacts/tmp'
    New-Item -ItemType Directory -Force -Path $rosLogs, $tmpDir | Out-Null
    $env:ROS_LOG_DIR = $rosLogs
    $env:TEMP = $tmpDir
    $env:TMP = $tmpDir
    $prepend = @(
        $portableEnv,
        (Join-Path $portableEnv 'Scripts'),
        (Join-Path $portableEnv 'Library/bin'),
        (Join-Path $portableEnv 'Library/usr/bin'),
        (Join-Path $portableEnv 'Library/mingw-w64/bin')
    ) -join ';'
    $env:PATH = "$prepend;$env:PATH"
    if ($Command.Count -eq 0) { exit 0 }
    $exe = $Command[0]
    $rest = if ($Command.Count -gt 1) { $Command[1..($Command.Count - 1)] } else { @() }
    & $exe @rest
    exit $LASTEXITCODE
}

$env:PIXI_CACHE_DIR = 'E:\simulator-cache\pixi'
$env:PIXI_HOME = 'E:\simulator-cache\pixi-home'
$env:PIXI_NO_CONFIG = '1'
$env:TEMP = 'E:\simulator-cache\tmp'
$env:TMP = $env:TEMP
$env:PYTHONNOUSERSITE = '1'
$env:ROS_LOG_DIR = 'E:\simulator-cache\ros-logs'
$env:COLCON_HOME = 'E:\simulator-cache\colcon-home'
& 'E:\simulator-tools\pixi\pixi.exe' run --manifest-path "$PSScriptRoot\pixi.toml" @Command
exit $LASTEXITCODE
