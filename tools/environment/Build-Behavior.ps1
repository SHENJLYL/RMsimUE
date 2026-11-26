$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$workspace = Join-Path $root 'ros2_ws'

function Require-BehaviorPackage($Name) {
    $source = Join-Path $workspace "src/$Name"
    if (!(Test-Path -LiteralPath $source -PathType Container)) {
        throw "Required local ROS 2 source directory missing: $source"
    }
    return $source
}

if (!(Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    $vcvars = $env:RMSIM_VCVARS
    if (!$vcvars) {
        $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
        if (Test-Path -LiteralPath $vswhere) {
            $vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
            if ($vs) { $vcvars = Join-Path $vs 'VC/Auxiliary/Build/vcvars64.bat' }
        }
    }
    if (!$vcvars -or !(Test-Path -LiteralPath $vcvars -PathType Leaf)) {
        throw 'Visual Studio x64 C++ tools are required. Run from a VS Developer PowerShell or set RMSIM_VCVARS.'
    }
    $compilerEnvironment = & cmd.exe /d /c "call `"$vcvars`" >nul && set"
    if ($LASTEXITCODE -ne 0) { throw "Could not activate the MSVC x64 toolchain from $vcvars" }
    foreach ($line in $compilerEnvironment) {
        $separator = $line.IndexOf('=')
        if ($separator -gt 0) {
            [Environment]::SetEnvironmentVariable($line.Substring(0, $separator), $line.Substring($separator + 1))
        }
    }
}

$behavior = Require-BehaviorPackage 'behavior'
$btRos = Require-BehaviorPackage 'BehaviorTree.ROS2'
$aimInterfaces = Require-BehaviorPackage 'auto_aim_interfaces'
$referee = Require-BehaviorPackage 'dji_referee_protocol'
$pbInterfaces = Require-BehaviorPackage 'pb_rm_interfaces'

$env:CMAKE_BUILD_PARALLEL_LEVEL = '4'
Push-Location $workspace
try {
    & (Join-Path $PSScriptRoot 'run-env.ps1') -Command @(
        'colcon', '--log-base', 'log_behavior', 'build',
        '--base-paths', $behavior, $btRos, $aimInterfaces, $referee, $pbInterfaces,
        '--packages-up-to', 'behavior', '--executor', 'sequential',
        '--build-base', 'build_behavior', '--install-base', 'install_behavior',
        '--cmake-args', '-G', 'Ninja', '-DBUILD_TESTING=OFF', '-DCMAKE_BUILD_TYPE=Release',
        '-DRMSIM_AUTO_AIM_INTERFACES_MINIMAL=ON')
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
