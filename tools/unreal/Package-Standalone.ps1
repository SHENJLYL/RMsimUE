param(
    [string]$EngineRoot = '',
    [string]$OutputDir = 'artifacts/release/RMSim-RMUC2027-Win64',
    [ValidateSet('Development', 'Shipping')][string]$Configuration = 'Development',
    [string]$RosEnvSource = '',
    [switch]$SkipBuild,
    [switch]$SkipCook,
    [switch]$IncludeRosStack,
    [switch]$OfflineAllInOne,
    [switch]$Zip
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($OfflineAllInOne) {
    $IncludeRosStack = $true
}

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
if (-not $EngineRoot) {
    $EngineRoot = if ($env:UE_ROOT) { $env:UE_ROOT } else { 'E:\simulator-tools\UE_5.8' }
}
. (Join-Path $PSScriptRoot 'Common.ps1')

function Resolve-RepoPath([string]$RawPath) {
    if ([IO.Path]::IsPathRooted($RawPath)) { return [IO.Path]::GetFullPath($RawPath) }
    return [IO.Path]::GetFullPath((Join-Path $root $RawPath))
}

function Copy-FileItem([string]$Source, [string]$Dest) {
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Required runtime file missing: $Source"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Dest) | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Dest -Force
}

function Copy-DirFiltered([string]$Source, [string]$Dest, [string[]]$ExcludeDirs = @('__pycache__', '.pixi'), [string[]]$ExcludeExts = @('.fbx', '.blend', '.blend1', '.skp', '.hdr', '.pyc')) {
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) { return }
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    $srcFull = [IO.Path]::GetFullPath($Source).TrimEnd('\', '/')
    Get-ChildItem -LiteralPath $srcFull -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($srcFull.Length).TrimStart('\', '/')
        $parts = $rel -split '[\\/]'
        foreach ($exDir in $ExcludeDirs) {
            if ($parts -contains $exDir) { return }
        }
        if ($ExcludeExts -contains $_.Extension.ToLowerInvariant()) { return }
        $target = Join-Path $Dest $rel
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $target -Force
    }
}

function Copy-Robocopy([string]$Source, [string]$Dest, [string[]]$ExtraArgs = @()) {
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "Required directory missing: $Source"
    }
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    $rcArgs = @($Source, $Dest, '/E', '/MT:16', '/NFL', '/NDL', '/NJH', '/NJS', '/NC', '/NS', '/NP') + $ExtraArgs
    & robocopy.exe @rcArgs | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed copying $Source -> $Dest (exit $LASTEXITCODE)" }
}

function Resolve-RosEnvSource([string]$ExplicitPath) {
    if ($ExplicitPath) {
        if (-not (Test-Path -LiteralPath (Join-Path $ExplicitPath 'python.exe') -PathType Leaf)) {
            throw "Invalid RosEnvSource (missing python.exe): $ExplicitPath"
        }
        return [IO.Path]::GetFullPath($ExplicitPath)
    }
    $candidates = @()
    $cfgFile = Join-Path $root 'tools/environment/.pixi/config.toml'
    if (Test-Path -LiteralPath $cfgFile -PathType Leaf) {
        $cfgText = Get-Content -LiteralPath $cfgFile -Raw -Encoding UTF8
        if ($cfgText -match 'detached-environments\s*=\s*"([^"]+)"') {
            $detachedRoot = $Matches[1]
            if (Test-Path -LiteralPath $detachedRoot -PathType Container) {
                $candidates += Get-ChildItem -LiteralPath $detachedRoot -Directory -Filter 'rm-simulator-ros2-humble-*' -ErrorAction SilentlyContinue |
                    ForEach-Object { Join-Path $_.FullName 'envs/default' }
            }
        }
    }
    $candidates += Join-Path $root 'tools/environment/.pixi/envs/default'
    foreach ($cand in $candidates) {
        if ($cand -and (Test-Path -LiteralPath (Join-Path $cand 'python.exe') -PathType Leaf)) {
            return [IO.Path]::GetFullPath($cand)
        }
    }
    throw 'Could not locate Pixi ROS 2 Humble environment. Pass -RosEnvSource explicitly.'
}

function Relocate-RosInstallScripts([string]$InstallRoot, [string]$OldRoot, [string]$OldEnv) {
    $envVariants = @(
        $OldEnv.TrimEnd('\', '/'),
        $OldEnv.TrimEnd('\', '/').Replace('\', '/'),
        $OldEnv.TrimEnd('\', '/').Replace('E:\simulator-cache\environments\', 'E:/simulator-cache/environments\')
    ) | Select-Object -Unique
    $rootVariants = @(
        $OldRoot.TrimEnd('\', '/'),
        $OldRoot.TrimEnd('\', '/').Replace('\', '/')
    ) | Select-Object -Unique

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    Get-ChildItem -LiteralPath $InstallRoot -Recurse -File | Where-Object {
        $_.Extension.ToLowerInvariant() -in '.bat', '.ps1'
    } | ForEach-Object {
        $text = [IO.File]::ReadAllText($_.FullName, [Text.Encoding]::UTF8)
        if ([string]::IsNullOrEmpty($text)) { return }
        $orig = $text
        $isBat = $_.Extension.ToLowerInvariant() -eq '.bat'
        $condaRepl = if ($isBat) { '%CONDA_PREFIX%' } else { '$env:CONDA_PREFIX' }
        $rootRepl = if ($isBat) { '%~dp0..\..' } else { '$((Resolve-Path (Join-Path $PSScriptRoot ''..\..'')).Path)' }
        foreach ($v in $envVariants) {
            if ($v) { $text = $text.Replace($v, $condaRepl) }
        }
        foreach ($r in $rootVariants) {
            if ($r) { $text = $text.Replace($r, $rootRepl) }
        }
        if ($text -cne $orig) {
            [IO.File]::WriteAllText($_.FullName, $text, $utf8NoBom)
        }
    }
}

$distDir = Resolve-RepoPath $OutputDir
$stageRoot = Join-Path $root 'artifacts/release/_ue_archive'
$buildTemp = Join-Path $root 'artifacts/unreal/build-temp'
New-Item -ItemType Directory -Force -Path $stageRoot, $buildTemp | Out-Null
$env:TEMP = $buildTemp
$env:TMP = $buildTemp

if (-not $SkipBuild -or -not $SkipCook) {
    $ue = Get-UnrealTools -EngineRoot $EngineRoot
    if (-not $SkipBuild) {
        Write-Output "=== Step 1a/3: Building RMSim ($Configuration Win64) ==="
        $buildLog = Join-Path $root 'artifacts/unreal/build-game.log'
        & $ue.Build RMSim Win64 $Configuration $ue.Project -WaitMutex -NoHotReloadFromIDE -NoUBA -MaxParallelActions=8 "-Log=$buildLog"
        if ($LASTEXITCODE -ne 0) {
            throw "RMSim $Configuration build failed with exit code $LASTEXITCODE"
        }
    }
    if (-not $SkipCook) {
        $runUat = Join-Path $ue.EngineRoot 'Engine/Build/BatchFiles/RunUAT.bat'
        if (-not (Test-Path -LiteralPath $runUat -PathType Leaf)) {
            throw "Missing RunUAT.bat at $runUat"
        }
        Write-Output "=== Step 1b/3: Running UE5 BuildCookRun ($Configuration Win64) ==="
        $uatArgs = @(
            'BuildCookRun',
            "-project=$($ue.Project)",
            '-noP4',
            '-platform=Win64',
            "-clientconfig=$Configuration",
            '-cook',
            '-AdditionalCookerOptions="-CustomConfig=RMSimGymLumen"',
            '-stage',
            '-pak',
            '-archive',
            "-archivedirectory=$stageRoot",
            '-nodebuginfo',
            '-prereqs',
            '-utf8output',
            '-unattended'
        )
        & $runUat @uatArgs
        if ($LASTEXITCODE -ne 0) {
            throw "BuildCookRun failed with exit code $LASTEXITCODE"
        }
    }
}

$archivedWindows = Join-Path $stageRoot 'Windows'
if (-not (Test-Path -LiteralPath (Join-Path $archivedWindows 'RMSim/Binaries/Win64/RMSim.exe') -PathType Leaf)) {
    throw "Packaged RMSim.exe not found under $archivedWindows"
}

Write-Output "=== Step 2/3: Assembling Standalone Distribution at $distDir ==="
if (Test-Path -LiteralPath $distDir) {
    Remove-Item -LiteralPath $distDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $distDir | Out-Null

# Place UE Windows archive at <distDir>/unreal so FPaths::ProjectDir() == <distDir>/unreal/RMSim/
# and ../../assets/ resolves cleanly to <distDir>/assets/ with zero C++ path changes.
Copy-Robocopy $archivedWindows (Join-Path $distDir 'unreal')

# Copy CustomConfig profile required by -CustomConfig=RMSimGymLumen and SceneMap.ps1.
Copy-FileItem `
    (Join-Path $root 'unreal/RMSim/Config/Custom/RMSimGymLumen/DefaultEngine.ini') `
    (Join-Path $distDir 'unreal/RMSim/Config/Custom/RMSimGymLumen/DefaultEngine.ini')

# Copy minimal runtime assets (no .fbx/.blend/.skp/.hdr or wt_vlaanderen).
$rmucSrc = Join-Path $root 'assets/maps/rmuc2027'
$rmucDst = Join-Path $distDir 'assets/maps/rmuc2027'
New-Item -ItemType Directory -Force -Path $rmucDst | Out-Null
Get-ChildItem -LiteralPath $rmucSrc -File -Filter '*.json' | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $rmucDst $_.Name) -Force
}
Copy-FileItem (Join-Path $rmucSrc 'dart-launcher/mount.json') (Join-Path $rmucDst 'dart-launcher/mount.json')
Copy-FileItem (Join-Path $rmucSrc 'arena/sketchupbox-school-gym/layout.json') (Join-Path $rmucDst 'arena/sketchupbox-school-gym/layout.json')
if (Test-Path -LiteralPath (Join-Path $rmucSrc 'arena/sketchupbox-school-gym/SOURCE.json') -PathType Leaf) {
    Copy-FileItem (Join-Path $rmucSrc 'arena/sketchupbox-school-gym/SOURCE.json') (Join-Path $rmucDst 'arena/sketchupbox-school-gym/SOURCE.json')
}
Copy-DirFiltered (Join-Path $rmucSrc 'supply-tags') (Join-Path $rmucDst 'supply-tags')

$vehSrc = Join-Path $root 'assets/vehicles/aim_sentry'
$vehDst = Join-Path $distDir 'assets/vehicles/aim_sentry'
New-Item -ItemType Directory -Force -Path $vehDst | Out-Null
Get-ChildItem -LiteralPath $vehSrc -File -Filter '*.json' | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $vehDst $_.Name) -Force
}
Copy-DirFiltered (Join-Path $vehSrc 'sensors') (Join-Path $vehDst 'sensors')
Copy-DirFiltered (Join-Path $vehSrc 'mujoco/rmuc2027') (Join-Path $vehDst 'mujoco/rmuc2027')

Copy-DirFiltered (Join-Path $root 'assets/ui') (Join-Path $distDir 'assets/ui')
Copy-DirFiltered (Join-Path $root 'assets/audio/rmuc2027') (Join-Path $distDir 'assets/audio/rmuc2027')

# Copy launcher and core Unreal scripts.
Copy-FileItem (Join-Path $root 'Play-Simulator.cmd') (Join-Path $distDir 'Play-Simulator.cmd')
Copy-FileItem (Join-Path $root 'tools/launcher/rmsim_launcher.py') (Join-Path $distDir 'tools/launcher/rmsim_launcher.py')
Copy-FileItem (Join-Path $root 'tools/unreal/Start-Simulator.ps1') (Join-Path $distDir 'tools/unreal/Start-Simulator.ps1')
Copy-FileItem (Join-Path $root 'tools/unreal/Common.ps1') (Join-Path $distDir 'tools/unreal/Common.ps1')
Copy-FileItem (Join-Path $root 'tools/unreal/SceneMap.ps1') (Join-Path $distDir 'tools/unreal/SceneMap.ps1')

if ($IncludeRosStack) {
    $resolvedRosEnv = Resolve-RosEnvSource $RosEnvSource
    Write-Output "Bundling ROS 2 autonomous sentry stack (source env: $resolvedRosEnv)..."

    # Navigation planning & localization maps.
    Copy-Robocopy (Join-Path $rmucSrc 'navigation-console-platform-support-v2') (Join-Path $rmucDst 'navigation-console-platform-support-v2')

    # Compiled ROS 2 workspaces (bridge, LIO, nav, control, behavior, vision).
    foreach ($wsName in @('install_behavior', 'install_bridge', 'install_control', 'install_lio', 'install_navigation', 'install_vision')) {
        $wsSrc = Join-Path $root ("ros2_ws/$wsName")
        if (Test-Path -LiteralPath $wsSrc -PathType Container) {
            Copy-Robocopy $wsSrc (Join-Path $distDir ("ros2_ws/$wsName")) @('/XD', '__pycache__', '/XF', '*.pdb', '*.lib', '*.exp', '*.pyc')
        }
    }

    # Runtime Python modules, nav2 configs, and sp_vision_25 neural weights.
    Copy-DirFiltered (Join-Path $root 'ros2_ws/src/rmsim_bridge') (Join-Path $distDir 'ros2_ws/src/rmsim_bridge')
    Copy-DirFiltered (Join-Path $root 'ros2_ws/src/sp_vision_25/assets') (Join-Path $distDir 'ros2_ws/src/sp_vision_25/assets')
    Copy-DirFiltered (Join-Path $root 'ros2_ws/src/sp_vision_25/sp_vision_25') (Join-Path $distDir 'ros2_ws/src/sp_vision_25/sp_vision_25')
    Copy-DirFiltered (Join-Path $root 'ros2_ws/src/pb2025_nav_bringup/config') (Join-Path $distDir 'ros2_ws/src/pb2025_nav_bringup/config')
    Copy-DirFiltered (Join-Path $root 'ros2_ws/src/pb2025_nav_bringup/behavior_trees') (Join-Path $distDir 'ros2_ws/src/pb2025_nav_bringup/behavior_trees')
    if (Test-Path -LiteralPath (Join-Path $root 'ros2_ws/src/rmsim_sentry_behavior') -PathType Container) {
        Copy-DirFiltered (Join-Path $root 'ros2_ws/src/rmsim_sentry_behavior') (Join-Path $distDir 'ros2_ws/src/rmsim_sentry_behavior')
    }

    # Staged native Nav2/AMCL/Behavior executables and classifier assets.
    Copy-Robocopy (Join-Path $root 'artifacts/runtime/navigation/windows') (Join-Path $distDir 'artifacts/runtime/navigation/windows') @('/XF', '*.pdb')
    if (Test-Path -LiteralPath (Join-Path $root 'artifacts/runtime/vision') -PathType Container) {
        Copy-DirFiltered (Join-Path $root 'artifacts/runtime/vision') (Join-Path $distDir 'artifacts/runtime/vision')
    }

    # Environment and integration runner scripts.
    Copy-DirFiltered (Join-Path $root 'tools/environment') (Join-Path $distDir 'tools/environment')
    Copy-DirFiltered (Join-Path $root 'tools/integration') (Join-Path $distDir 'tools/integration')

    # Relocate hardcoded dev paths in install_* setup scripts to portable %CONDA_PREFIX% / relative root.
    Relocate-RosInstallScripts (Join-Path $distDir 'ros2_ws') $root $resolvedRosEnv

    if ($OfflineAllInOne) {
        Write-Output "Copying self-contained offline ROS 2 Humble runtime to runtime/ros2_env (trimming build-only headers/libs)..."
        $excludeDirs = @(
            (Join-Path $resolvedRosEnv 'conda-meta'),
            (Join-Path $resolvedRosEnv '__clobbers__'),
            (Join-Path $resolvedRosEnv 'include'),
            (Join-Path $resolvedRosEnv 'libs'),
            (Join-Path $resolvedRosEnv 'Library\include'),
            (Join-Path $resolvedRosEnv 'Library\doc'),
            (Join-Path $resolvedRosEnv 'Library\src'),
            (Join-Path $resolvedRosEnv 'Library\mkspecs'),
            (Join-Path $resolvedRosEnv 'Library\cmake'),
            (Join-Path $resolvedRosEnv 'Library\lib\cmake'),
            '__pycache__'
        )
        $rcExtra = @('/XD') + $excludeDirs + @('/XF', '*.lib', '*.pdb', '*.exp', '*.pyc')
        Copy-Robocopy $resolvedRosEnv (Join-Path $distDir 'runtime/ros2_env') $rcExtra
    }
}

$editionLabel = if ($OfflineAllInOne) { 'Offline All-in-One Edition (UE5 + MuJoCo + Dual Autonomous Sentries ROS 2)' } else { 'Standalone Edition (UE5 + MuJoCo)' }
$reqText = if ($OfflineAllInOne) {
    "- Windows 10 / 11 (64-bit) + 支持 DX12 (SM6) 的独立显卡`r`n- 无需安装 Unreal Engine 5、Python、Pixi 或 ROS 2（全套运行时已离线内置）"
} else {
    "- Windows 10 / 11 (64-bit) + 支持 DX12 (SM6) 的独立显卡`r`n- Python 3 (用于打开桌面启动器 UI，无需安装 Unreal Engine 5)"
}
$readme = @"
RMSim RMUC 2027 - $editionLabel
=====================================================================

运行要求:
$reqText
- 如首次在纯净 Windows 运行报缺 VC++/DirectX 运行库，请先运行一次:
  unreal\Engine\Extras\Redist\en-us\UEPrereqSetup_x64.exe

启动方式:
1. 解压到任意不含中文空格的路径（如 D:\RMSim\）
2. 双击根目录下的 Play-Simulator.cmd
3. 比赛模式默认配置:
   - 6 车完整 RMUC 2027 3v3（红蓝 #7 全自动哨兵 + #3/#4 步兵 AI）
   - 100% 渲染比例 + TSR + 关闭主视角 Lumen + GPU 满血雷达（200k×2）
   - 键位: WASD 移动 | 鼠标瞄准 | 左键发射 | 右键开镜 | C 自由视角 | F 补给/买弹 | P 跳过赛前准备
"@
Set-Content -LiteralPath (Join-Path $distDir 'README.txt') -Value $readme -Encoding UTF8

$sizeMb = [Math]::Round(((Get-ChildItem -LiteralPath $distDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Output "Standalone folder ready: $distDir ($sizeMb MB)"

if ($Zip) {
    Write-Output "=== Step 3/3: Creating Zip Archive ==="
    $zipPath = $distDir.TrimEnd('\', '/') + '.zip'
    if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
    $parentDir = Split-Path -Parent $distDir
    $leafName = Split-Path -Leaf $distDir
    Push-Location $parentDir
    try {
        & tar.exe -a -cf $zipPath $leafName
        if ($LASTEXITCODE -ne 0) { throw "tar.exe zip creation failed (exit $LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
    $zipMb = [Math]::Round(((Get-Item -LiteralPath $zipPath).Length / 1MB), 1)
    Write-Output "Zip archive ready: $zipPath ($zipMb MB)"
}
