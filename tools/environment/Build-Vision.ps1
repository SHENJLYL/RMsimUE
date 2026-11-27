param([switch]$Clean)
$ErrorActionPreference = 'Stop'

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$ws = Join-Path $root 'ros2_ws'

if ($Clean) {
    foreach ($dir in @('build_vision', 'install_vision', 'log_vision')) {
        $full = Join-Path $ws $dir
        if (Test-Path $full) {
            Remove-Item -Recurse -Force $full
        }
    }
}

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vsPath) {
    throw 'Visual Studio x64 C++ toolchain not found.'
}
$vcvars = Join-Path $vsPath 'VC\Auxiliary\Build\vcvars64.bat'
cmd /c "`"$vcvars`" >nul && set" | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}

# Expose upstream ROS 2 message overlays (sp_msgs + dji_referee_protocol) to CMake
$overlayPrefixes = @(
    (Join-Path $ws 'install_control/sp_msgs'),
    (Join-Path $ws 'install_control/pb_rm_interfaces'),
    (Join-Path $ws 'install_behavior/dji_referee_protocol')
) | Where-Object { Test-Path $_ }

$existingPrefix = $env:CMAKE_PREFIX_PATH
$env:CMAKE_PREFIX_PATH = ($overlayPrefixes -join ';') + $(if ($existingPrefix) { ';' + $existingPrefix } else { '' })
$existingAment = $env:AMENT_PREFIX_PATH
$env:AMENT_PREFIX_PATH = ($overlayPrefixes -join ';') + $(if ($existingAment) { ';' + $existingAment } else { '' })

$buildCmd = @(
    'colcon',
    '--log-base', (Join-Path $ws 'log_vision'),
    'build',
    '--base-paths', (Join-Path $ws 'src/sp_vision_25'),
    '--build-base', (Join-Path $ws 'build_vision'),
    '--install-base', (Join-Path $ws 'install_vision'),
    '--packages-select', 'sp_vision_25',
    '--cmake-args', '-G', 'Ninja', '-DBUILD_TESTING=OFF', '-DCMAKE_BUILD_TYPE=Release',
    '--event-handlers', 'console_direct+'
)

$env:PIXI_CACHE_DIR = 'E:\simulator-cache\pixi'
$env:PIXI_HOME = 'E:\simulator-cache\pixi-home'
$env:PIXI_NO_CONFIG = '1'
$env:TEMP = 'E:\simulator-cache\tmp'
$env:TMP = $env:TEMP
$env:PYTHONNOUSERSITE = '1'
$env:ROS_LOG_DIR = 'E:\simulator-cache\ros-logs'
$env:COLCON_HOME = 'E:\simulator-cache\colcon-home'

Push-Location $ws
try {
    & 'E:\simulator-tools\pixi\pixi.exe' run --manifest-path (Join-Path $PSScriptRoot 'pixi.toml') @buildCmd
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $code
