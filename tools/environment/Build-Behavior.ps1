$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$workspace = Join-Path $root 'ros2_ws'
$patches = Join-Path $root 'third_party/patches'

function Import-BehaviorSource($Name, $Url, $Commit, $Patch) {
    $source = Join-Path $workspace "src/$Name"
    if (!(Test-Path -LiteralPath $source)) {
        & git init --quiet $source
        if ($LASTEXITCODE -ne 0) { throw "Could not initialize $source" }
        & git -C $source remote add origin $Url
        if ($LASTEXITCODE -ne 0) { throw "Could not set origin for $source" }
    }
    if (!(Test-Path -LiteralPath (Join-Path $source '.git'))) {
        throw "$source must be a dedicated Git checkout of $Url"
    }
    $current = & git -C $source rev-parse --verify HEAD 2>$null
    if ($LASTEXITCODE -ne 0) {
        & git -C $source fetch --quiet --depth 1 origin $Commit
        if ($LASTEXITCODE -ne 0) { throw "Could not fetch $Name at $Commit" }
        & git -C $source checkout --quiet --detach $Commit
        if ($LASTEXITCODE -ne 0) { throw "Could not check out $Name at $Commit" }
        $current = & git -C $source rev-parse HEAD
    }
    if ($current -ne $Commit) {
        throw "$source is at $current, expected $Commit. Preserve local edits before replacing it."
    }
    if ($Patch) {
        $patchFile = Join-Path $patches $Patch
        & git -C $source apply --reverse --check $patchFile 2>$null
        if ($LASTEXITCODE -ne 0) {
            & git -C $source apply $patchFile
            if ($LASTEXITCODE -ne 0) { throw "Could not apply $Patch to $Name" }
        }
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

$behavior = Import-BehaviorSource 'behavior' 'https://github.com/unnc-aim/27UL_Sentry_behavior.git' `
    'aa4d82b73ae3c404b3037f2630eb2950dc0bbea4' 'sentry-behavior-native.patch'
$btRos = Import-BehaviorSource 'BehaviorTree.ROS2' 'https://github.com/unnc-aim/BehaviorTree.ROS2.git' `
    '5e3979e16bcfd7369c8df6d2c903e89e86f9a80b' 'behaviortree-ros2-native.patch'
$aimInterfaces = Import-BehaviorSource 'auto_aim_interfaces' 'https://github.com/SMBU-PolarBear-Robotics-Team/auto_aim_interfaces.git' `
    '5eab0e0464b6cfae7cf8ae0599109b3f5b623e3d' 'auto-aim-interfaces-native.patch'
$referee = Import-BehaviorSource 'dji_referee_protocol' 'https://github.com/unnc-aim/26UL_dji_referee_protocol.git' `
    'cf28347bfc284930cc68b206c41f171b0fe32bfc' ''
$pbInterfaces = Join-Path $workspace 'src/pb_rm_interfaces'
$tactics = Join-Path $workspace 'src/rmsim_sentry_tactics'

$env:CMAKE_BUILD_PARALLEL_LEVEL = '4'
Push-Location $workspace
try {
    & (Join-Path $PSScriptRoot 'run-env.ps1') -Command @(
        'colcon', '--log-base', 'log_behavior', 'build',
        '--base-paths', $behavior, $btRos, $aimInterfaces, $referee, $pbInterfaces, $tactics,
        '--packages-up-to', 'behavior', 'rmsim_sentry_tactics', '--executor', 'sequential',
        '--build-base', 'build_behavior', '--install-base', 'install_behavior',
        '--cmake-args', '-G', 'Ninja', '-DBUILD_TESTING=OFF', '-DCMAKE_BUILD_TYPE=Release',
        '-DRMSIM_AUTO_AIM_INTERFACES_MINIMAL=ON')
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
