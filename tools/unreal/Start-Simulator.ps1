param(
    [string]$EngineRoot = '',
    [string]$Vehicle = 'assets/vehicles/aim_sentry',
    [string]$Map = 'assets/maps/rmuc2027',
    [string]$SpawnId = 'default',
    [string]$RobotId = '',
    [ValidateSet('mujoco', 'chaos')][string]$PhysicsBackend = 'mujoco',
    [double]$SimStepSeconds = 0.005,
    [ValidateScript({$_ -eq 0 -or ($_ -ge 10 -and $_ -le 144)})][double]$ViewportHz = 120,
    [ValidateRange(50, 100)][int]$RenderScale = 50,
    [switch]$TrainingMode,
    [string]$MatchRoster = '',
    [switch]$MatchPreparation,
    [ValidateSet('red', 'blue')][string]$RosVehicleTeam,
    [switch]$AutoSentry,
    [string]$NavigationSource = '',
    [switch]$RosBridge,
    [switch]$RosControl,
    [switch]$RosAimControl,
    [switch]$RosImu,
    [switch]$RosLidar,
    [int]$RosPort = 9451,
    [int]$Domain = 71,
    [double]$CameraHz = 0,
    [int]$CameraWidth = 1280,
    [int]$CameraHeight = 720,
    [string]$InputEvidence = '',
    [string]$HudCapturePath = '',
    [switch]$RefereeProjectileTrace,
    [int]$RuneClientBridgePort = 0,
    [switch]$Offscreen,
    [switch]$NoBridgeNode,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
if (-not $EngineRoot) {
    $EngineRoot = if ($env:UE_ROOT) { $env:UE_ROOT } else { 'E:\simulator-tools\UE_5.8' }
}
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'SceneMap.ps1')

function Resolve-RepoPath([string]$RawPath) {
    if ([IO.Path]::IsPathRooted($RawPath)) { return [IO.Path]::GetFullPath($RawPath) }
    return [IO.Path]::GetFullPath((Join-Path $root $RawPath))
}

$vehiclePath = Resolve-RepoPath $Vehicle
$mapPath = Resolve-RepoPath $Map
if (Test-Path -LiteralPath $mapPath -PathType Container) {
    $mapPath = Join-Path $mapPath 'map.json'
}
if (-not (Test-Path -LiteralPath $mapPath -PathType Leaf)) {
    throw "Map manifest not found: $mapPath"
}
$mapJson = Get-Content -LiteralPath $mapPath -Raw -Encoding UTF8 | ConvertFrom-Json
$mapId = [string]$mapJson.id
$mapKind = [string]$mapJson.kind

if ($RosVehicleTeam -and -not $MatchRoster) { throw 'RosVehicleTeam requires MatchRoster.' }
if ($MatchPreparation -and -not $MatchRoster) { throw 'MatchPreparation requires MatchRoster.' }
if ($AutoSentry -and (-not $RosVehicleTeam -or $NoBridgeNode)) {
    throw 'AutoSentry requires a match ROS vehicle and the standalone bridge node.'
}
$rosSpawn = ''
if ($MatchRoster) {
    $MatchRoster = Resolve-RepoPath $MatchRoster
    if (-not (Test-Path -LiteralPath $MatchRoster -PathType Leaf)) { throw "Missing match roster: $MatchRoster" }
    if ($mapId -cne 'rmuc2027' -or $mapKind -cne 'rm_mesh_map' -or $TrainingMode) {
        throw 'MatchRoster requires the RMUC 2027 mesh map in match mode.'
    }
    if ($RosVehicleTeam) {
        $RosVehicleTeam = $RosVehicleTeam.ToLowerInvariant()
        $spawns = @($mapJson.spawns | Where-Object { $_.team -ceq $RosVehicleTeam })
        if ($spawns.Count -ne 1) { throw "Expected one match spawn for the $RosVehicleTeam ROS vehicle." }
        $rosSpawn = [string]$spawns[0].id
        $RosBridge = $RosControl = $RosAimControl = $RosImu = $RosLidar = $true
        if ($CameraHz -eq 0) { $CameraHz = 15 }
        if ($AutoSentry -and $NavigationSource) {
            $NavigationSource = Resolve-RepoPath $NavigationSource
            if (-not (Test-Path -LiteralPath $NavigationSource -PathType Leaf)) {
                throw "External sentry_ws nav2_params.yaml not found: $NavigationSource"
            }
        }
    } elseif ($RosBridge -or $RosControl -or $RosAimControl -or $RosImu -or $RosLidar) {
        throw 'Match ROS flags require RosVehicleTeam.'
    }
}

$isMeshRig = $false
$vehicleInputFile = ''
$vehicleId = ''
if (Test-Path -LiteralPath $vehiclePath -PathType Container) {
    $meshRigCandidate = Join-Path $vehiclePath 'ue_mesh_rig.json'
    $canonicalCandidate = Join-Path $vehiclePath 'vehicle.canonical.json'
    $manifestCandidate = Join-Path $vehiclePath 'vehicle.json'
    if (Test-Path -LiteralPath $meshRigCandidate -PathType Leaf) {
        $isMeshRig = $true
        $vehicleInputFile = $meshRigCandidate
    } elseif (Test-Path -LiteralPath $canonicalCandidate -PathType Leaf) {
        $vehicleInputFile = $canonicalCandidate
    } elseif (Test-Path -LiteralPath $manifestCandidate -PathType Leaf) {
        $vehicleInputFile = $manifestCandidate
    } else {
        throw "No ue_mesh_rig.json, vehicle.canonical.json, or vehicle.json found in: $vehiclePath"
    }
} else {
    if (-not (Test-Path -LiteralPath $vehiclePath -PathType Leaf)) {
        throw "Vehicle path not found: $vehiclePath"
    }
    $vehicleInputFile = $vehiclePath
    if ($vehicleInputFile -like '*ue_mesh_rig.json') {
        $isMeshRig = $true
    }
}

$pyVenv = Join-Path $root 'artifacts/environments/mujoco-4.0.1/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pyVenv -PathType Leaf)) {
    $pyVenv = (Get-Command python -ErrorAction Stop).Source
}

# If a vehicle.json (manifest) was passed instead of vehicle.canonical.json, export canonical JSON first.
if (-not $isMeshRig -and (Split-Path -Leaf $vehicleInputFile) -eq 'vehicle.json') {
    $vehicleDir = Split-Path -Parent $vehicleInputFile
    $exportedCanonical = Join-Path $root ('artifacts/vehicles/canonical/' + (Split-Path -Leaf $vehicleDir) + '/vehicle.canonical.json')
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $exportedCanonical) | Out-Null
    $env:PYTHONPATH = Join-Path $root 'src'
    & $pyVenv -m rm_vehicle_tools export $vehicleInputFile --output $exportedCanonical
    if ($LASTEXITCODE -ne 0) { throw "Failed to export canonical vehicle JSON from $vehicleInputFile" }
    $vehicleInputFile = $exportedCanonical
}

$vehicleJson = Get-Content -LiteralPath $vehicleInputFile -Raw -Encoding UTF8 | ConvertFrom-Json
$vehicleId = [string]$vehicleJson.id
if ($MatchRoster -and ($vehicleId -cne 'aim_sentry' -or -not $isMeshRig -or $PhysicsBackend -ne 'mujoco')) {
    throw 'Match mode requires the AIM S7 MuJoCo vehicle package.'
}
if (-not $RobotId) { $RobotId = $vehicleId }
if ($RobotId -cnotmatch '^[A-Za-z][A-Za-z0-9_]{0,63}$') { throw "Invalid robot id: $RobotId" }

$mujocoModel = ''
if ($mapKind -eq 'rm_scene_map') {
    $PhysicsBackend = 'chaos'
} elseif ($PhysicsBackend -eq 'mujoco') {
    if ($isMeshRig) {
        $mujocoModel = Join-Path (Split-Path -Parent $vehicleInputFile) ("mujoco/$mapId/manifest.json")
        if (-not (Test-Path -LiteralPath $mujocoModel -PathType Leaf)) {
            if ($MatchRoster) { throw "Match mode requires the prepared MuJoCo model: $mujocoModel" }
            Write-Output "No prebuilt MuJoCo model for mesh-rig '$vehicleId' on map '$mapId'; automatically falling back to Chaos physics backend."
            $PhysicsBackend = 'chaos'
            $mujocoModel = ''
        }
    } else {
        $safeSpawn = $SpawnId -replace '[^A-Za-z0-9_-]', '_'
        $composedDir = Join-Path $root ("artifacts/mujoco/composed/${vehicleId}__${mapId}__${safeSpawn}")
        Write-Output "Composing decoupled MuJoCo scene: vehicle=$vehicleId map=$mapId spawn=$SpawnId"
        & $pyVenv (Join-Path $root 'tools/mujoco/compose_scene.py') `
            --repo $root `
            --vehicle $vehicleInputFile `
            --map $mapPath `
            --spawn $SpawnId `
            --output $composedDir `
            --overwrite
        if ($LASTEXITCODE -ne 0) { throw "Failed to compose decoupled MuJoCo scene for $vehicleId on $mapId" }
        $mujocoModel = Join-Path $composedDir 'manifest.json'
    }
}

$ue = Get-UnrealTools -EngineRoot $EngineRoot
$mapLaunch = Get-RMSimMapLaunch -MapFile $mapPath -Project $ue.Project -SpawnId $SpawnId

$ueArgs = @(
    (Quote-UnrealArgument $ue.Project),
    $mapLaunch.WorldUrl,
    '-game',
    '-nosplash'
)
if ($Offscreen) {
    $ueArgs += @('-RenderOffScreen', '-NoSound', '-ResX=640', '-ResY=480')
} else {
    $ueArgs += @('-windowed', '-ResX=1600', '-ResY=900')
}
if ($isMeshRig) {
    $ueArgs += "-MeshRigFile=$vehicleInputFile"
} else {
    $ueArgs += "-VehicleFile=$vehicleInputFile"
}
$ueArgs += "-MapFile=$($mapLaunch.MapFile)"
$ueArgs += "-SpawnId=$SpawnId"
$ueArgs += '-ContactPhysics'
$ueArgs += "-PhysicsBackend=$PhysicsBackend"
if ($mujocoModel) {
    $ueArgs += "-MujocoModel=$mujocoModel"
}
if ($SimStepSeconds -gt 0) {
    $ueArgs += ('-SimStepSeconds=' + $SimStepSeconds.ToString([Globalization.CultureInfo]::InvariantCulture))
}
$ueArgs += '-SimRealTimePace'
if ($ViewportHz -gt 0) {
    $ueArgs += ('-RMSimViewportHz=' + $ViewportHz.ToString([Globalization.CultureInfo]::InvariantCulture))
}
if ($RenderScale -ne 100) {
    $ueArgs += ('-ExecCmds=' + (Quote-UnrealArgument "r.ScreenPercentage $RenderScale"))
}
$ueArgs += @(Get-RMSimArenaRenderArguments -MapFile $mapLaunch.MapFile -Project $ue.Project)
if ($TrainingMode) { $ueArgs += '-TrainingMode' }
if ($MatchRoster) { $ueArgs += ('-MatchRoster=' + (Quote-UnrealArgument $MatchRoster)) }
if ($MatchPreparation) { $ueArgs += '-RefereePreparationSeconds=180' }
$navigationFollowProfile = ''
if ($AutoSentry) {
    $navigationFollowProfile = Join-Path (Split-Path -Parent $vehicleInputFile) 'navigation-follow.json'
    if (-not (Test-Path -LiteralPath $navigationFollowProfile -PathType Leaf)) {
        throw "Missing match navigation follow profile: $navigationFollowProfile"
    }
    $horizontalFov = [double]$vehicleJson.camera.parameters.horizontal_fov_rad * 180.0 / [Math]::PI
    if ([double]::IsNaN($horizontalFov) -or $horizontalFov -lt 5 -or $horizontalFov -gt 150) {
        throw 'Invalid match camera horizontal field of view.'
    }
    $ueArgs += @('-RmsimMatchAutonomy', ('-RosCameraHFov=' + $horizontalFov.ToString([Globalization.CultureInfo]::InvariantCulture)))
}
if ($RosVehicleTeam) { $ueArgs += "-RosVehicleTeam=$RosVehicleTeam" }

$enableRos = $RosBridge -or $RosControl
if ($enableRos) {
    $ueArgs += '-RosBridge'
    if ($RosControl) {
        $ueArgs += '-RosControl'
        if (-not $MatchRoster) {
            $ueArgs += @('-RosOperatorGyroControl', '-RosOperatorGyroStart', '-RosOperatorGyroStartRate=5.0')
        } elseif ($AutoSentry) {
            $ueArgs += @('-RosOperatorGyroStart', '-RosOperatorGyroStartRate=5.0')
        }
    }
    if ($RosAimControl) { $ueArgs += '-RosAimControl' }
    $vehiclePackage = if (Test-Path -LiteralPath $vehiclePath -PathType Container) { $vehiclePath } else { Split-Path -Parent $vehiclePath }
    if ($RosImu) {
        $mid360Imu = Join-Path (Split-Path -Parent $vehicleInputFile) 'sensors/mid360-imu.json'
        $imu = if ($isMeshRig -and (Test-Path -LiteralPath $mid360Imu -PathType Leaf)) { $mid360Imu } else { Join-Path $vehiclePackage 'sensors/imu.json' }
        if (Test-Path -LiteralPath $imu -PathType Leaf) {
            $ueArgs += @('-RosImu', ('-ImuProfile=' + (Quote-UnrealArgument $imu)))
        } else {
            if ($MatchRoster) { throw "Match ROS IMU requires the runtime profile: $imu" }
            Write-Warning "ROS IMU unavailable for '$vehicleId': missing runtime profile $imu. Continuing with the other ROS interfaces."
        }
    }
    if ($RosLidar) {
        $mid360Lidar = Join-Path (Split-Path -Parent $vehicleInputFile) 'sensors/mid360.json'
        $lidar = if ($isMeshRig -and (Test-Path -LiteralPath $mid360Lidar -PathType Leaf)) { $mid360Lidar } else { Join-Path $vehiclePackage 'sensors/lidar.json' }
        if (Test-Path -LiteralPath $lidar -PathType Leaf) {
            $ueArgs += @('-RosLidar', ('-LidarProfile=' + (Quote-UnrealArgument $lidar)))
        } else {
            if ($MatchRoster) { throw "Match ROS LiDAR requires the runtime profile: $lidar" }
            Write-Warning "ROS LiDAR unavailable for '$vehicleId': missing runtime profile $lidar. Continuing with the other ROS interfaces."
        }
    }
    $ueArgs += "-RosPort=$RosPort"
    $ueArgs += "-RosRobotId=$RobotId"
    $ueArgs += "-RosCameraWidth=$CameraWidth"
    $ueArgs += "-RosCameraHeight=$CameraHeight"
    $ueArgs += ('-RosCameraHz=' + $CameraHz.ToString([Globalization.CultureInfo]::InvariantCulture))
}
if ($InputEvidence) { $ueArgs += ('-InputEvidence=' + (Quote-UnrealArgument (Resolve-RepoPath $InputEvidence))) }
if ($HudCapturePath) { $ueArgs += ('-HudCapturePath=' + (Quote-UnrealArgument (Resolve-RepoPath $HudCapturePath))) }
if ($RefereeProjectileTrace) { $ueArgs += '-RefereeProjectileTrace' }
if ($RuneClientBridgePort) {
    if ($mapId -cne 'rmuc2027' -or $TrainingMode -or $RuneClientBridgePort -lt 1024 -or $RuneClientBridgePort -gt 65535) {
        throw 'RuneClientBridgePort requires a valid local port and the RMUC 2027 competition.'
    }
    if (-not $DryRun -and (-not $env:RMSIM_RUNE_BRIDGE_TOKEN -or $env:RMSIM_RUNE_BRIDGE_TOKEN.Length -lt 16)) {
        throw 'Set the private RMSIM_RUNE_BRIDGE_TOKEN before opening the rune client gateway.'
    }
    $ueArgs += "-RuneClientBridgePort=$RuneClientBridgePort"
}
foreach ($extra in $mapLaunch.RuntimeArguments) {
    $ueArgs += $extra
}

Write-Output "=== RMSim Pure Simulator Launch ==="
Write-Output "Vehicle  : $vehicleId ($vehicleInputFile)"
Write-Output "Map      : $mapId ($mapKind, spawn=$SpawnId)"
Write-Output "Physics  : $PhysicsBackend $(if($mujocoModel){"[$mujocoModel]"})"
if ($MatchRoster) {
    Write-Output "Roster   : $MatchRoster"
    if ($RosVehicleTeam) { Write-Output "ROS seat : $RosVehicleTeam (spawn=$rosSpawn, auto_sentry=$([bool]$AutoSentry))" }
}
if ($enableRos) {
    Write-Output "ROS2 API : robot_id=$RobotId port=$RosPort domain=$Domain control=$([bool]$RosControl)"
    Write-Output "Topics   : /clock, /$RobotId/cmd_vel, /$RobotId/camera/image_raw, /$RobotId/gimbal/joint_states, /tf"
}

if ($DryRun) {
    Write-Output ('UE Command: ' + $ue.Editor + ' ' + ($ueArgs -join ' '))
    exit 0
}

function Test-LaunchLog([string]$Path, [string]$Pattern) {
    return (Test-Path -LiteralPath $Path -PathType Leaf) -and
        (Select-String -LiteralPath $Path -Quiet -Pattern $Pattern -ErrorAction SilentlyContinue)
}

$bridgeProc = $null
$sentryProc = $null
$ueLogDir = Join-Path $root ('artifacts/unreal/pure-simulator/' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $ueLogDir -Force | Out-Null
$ueLogPath = Join-Path $ueLogDir 'ue.log'
$ueArgs += ('-AbsLog=' + (Quote-UnrealArgument $ueLogPath))
try {
    $windowStyle = if ($Offscreen) { 'Hidden' } else { 'Normal' }
    $ueProc = Start-Process -FilePath $ue.Editor -ArgumentList $ueArgs -WindowStyle $windowStyle -PassThru
    Write-Output "Started UE5 Simulator (PID $($ueProc.Id))"
    Write-Output "UE logs: $ueLogPath"

    $ueRosReady = $false
    if ($enableRos -and -not $NoBridgeNode) {
        $ueRosReady = -not $MatchRoster
        if ($MatchRoster) {
            Write-Output "Waiting for this match's UE5 ROS bridge on port $RosPort..."
            $deadline = [DateTime]::UtcNow.AddSeconds(120)
            while (-not $ueProc.HasExited -and [DateTime]::UtcNow -lt $deadline) {
                if (Test-LaunchLog $ueLogPath ('ROS_BRIDGE_READY loopback=127\.0\.0\.1:' + $RosPort + ' ')) {
                    $ueRosReady = $true
                    break
                }
                [void]$ueProc.WaitForExit(500)
            }
            if (-not $ueRosReady -and -not $ueProc.HasExited) {
                Write-Warning "Timed out waiting for the match ROS bridge. See $ueLogPath"
            }
        }
    }

    if ($ueRosReady -and -not $ueProc.HasExited) {
        $bridgeLogDir = Join-Path $root ('artifacts/ros2/simulator-bridge-' + $ueProc.Id)
        New-Item -ItemType Directory -Force -Path $bridgeLogDir | Out-Null
        $bridgeOut = Join-Path $bridgeLogDir 'stdout.log'
        $bridgeErr = Join-Path $bridgeLogDir 'stderr.log'
        $bridgeArgs = @('-NoProfile','-File',(Quote-UnrealArgument (Join-Path $root 'tools/environment/Start-RosBridge.ps1')),
            '-RobotId',$RobotId,'-Domain',$Domain,'-Port',$RosPort)
        if ($ueArgs -contains '-RosLidar') { $bridgeArgs += '-Lidar' }
        if ($RosAimControl) { $bridgeArgs += '-AimControl' }
        if ($navigationFollowProfile) { $bridgeArgs += @('-NavigationFollowProfile',(Quote-UnrealArgument $navigationFollowProfile)) }
        $bridgeProc = Start-Process -FilePath 'powershell.exe' -ArgumentList $bridgeArgs -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $bridgeOut -RedirectStandardError $bridgeErr
        Write-Output "Started standalone rmsim_bridge ROS 2 node (PID $($bridgeProc.Id)) for robot_id=$RobotId on port=$RosPort"
        Write-Output "External robot code can connect via ROS_DOMAIN_ID=$Domain (use_sim_time:=true)."
        Write-Output "Bridge logs: $bridgeLogDir"

        $attach = [bool]$AutoSentry
        $deadline = [DateTime]::UtcNow.AddSeconds(90)
        while (-not $ueProc.WaitForExit(500)) {
            if ($bridgeProc.HasExited) {
                Write-Warning "ROS 2 bridge exited (code=$($bridgeProc.ExitCode)). See $bridgeLogDir"
                break
            }
            if ($attach) {
                $readyPattern = 'RMSim bridge listening to local simulator state; waiting for TCP 127\.0\.0\.1:' + $RosPort + '$'
                if ((Test-LaunchLog $bridgeErr $readyPattern) -or (Test-LaunchLog $bridgeOut $readyPattern)) {
                    $attach = $false
                    $sentryArgs = @('-NoProfile','-File',(Quote-UnrealArgument (Join-Path $root 'tools/environment/Start-ExternalSentry.ps1')),
                        '-RobotId',$RobotId,'-Domain',$Domain,'-MapFile',(Quote-UnrealArgument $mapLaunch.MapFile),'-SpawnId',$rosSpawn,
                        '-MatchAutonomy')
                    if ($NavigationSource) { $sentryArgs += @('-NavigationSource',(Quote-UnrealArgument $NavigationSource)) }
                    $sentryProc = Start-Process -FilePath 'powershell.exe' -ArgumentList $sentryArgs -WindowStyle Hidden -PassThru `
                        -RedirectStandardOutput (Join-Path $ueLogDir 'sentry.stdout.log') -RedirectStandardError (Join-Path $ueLogDir 'sentry.stderr.log')
                    Write-Output "Started sentry_ws localization and navigation (PID $($sentryProc.Id)) for $RosVehicleTeam at spawn=$rosSpawn. Logs: $ueLogDir"
                } elseif ([DateTime]::UtcNow -ge $deadline) {
                    $attach = $false
                    Write-Warning "Timed out waiting for the ROS 2 bridge node. See $bridgeLogDir"
                }
            }
            if ($sentryProc -and $sentryProc.HasExited) {
                Write-Warning "The sentry_ws connector exited (code=$($sentryProc.ExitCode)). See $ueLogDir"
                $sentryProc = $null
            }
        }
    }

    $ueProc.WaitForExit()
    if (Test-Path -LiteralPath $ueLogPath -PathType Leaf) {
        $failure = Select-String -LiteralPath $ueLogPath -Pattern 'LogRMSim: Error: IMPORT_LAB_FAILED (.+)' | Select-Object -Last 1
        if ($failure) {
            [Console]::Error.WriteLine('UE5 initialization failed: ' + $failure.Matches[0].Groups[1].Value)
            exit 2
        }
    }
    exit $ueProc.ExitCode
} finally {
    if ($sentryProc -and -not $sentryProc.HasExited) {
        Write-Output "Stopping sentry_ws (PID $($sentryProc.Id))..."
        & taskkill.exe /PID $sentryProc.Id /T /F | Out-Null
    }
    if ($bridgeProc -and -not $bridgeProc.HasExited) {
        Write-Output "Stopping rmsim_bridge (PID $($bridgeProc.Id))..."
        & taskkill.exe /PID $bridgeProc.Id /T /F | Out-Null
    }
}
