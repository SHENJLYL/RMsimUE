param(
    [string]$EngineRoot = '',
    [string]$Vehicle = 'assets/vehicles/aim_sentry',
    [string]$Map = 'assets/maps/rmuc2027',
    [string]$SpawnId = 'default',
    [string]$RobotId = '',
    [ValidateSet('mujoco', 'chaos')][string]$PhysicsBackend = 'mujoco',
    [double]$SimStepSeconds = 0.005,
    [double]$ViewportHz = 120,
    [ValidateRange(50, 100)][int]$RenderScale = 50,
    [switch]$TrainingMode,
    [switch]$RosBridge,
    [switch]$RosControl,
    [switch]$RosAimControl,
    [switch]$RosImu,
    [switch]$RosLidar,
    [int]$RosPort = 9451,
    [int]$Domain = 71,
    [double]$CameraHz = 0,
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
if (-not $RobotId) { $RobotId = $vehicleId }
if ($RobotId -cnotmatch '^[A-Za-z][A-Za-z0-9_]{0,63}$') { throw "Invalid robot id: $RobotId" }

$mujocoModel = ''
if ($mapKind -eq 'rm_scene_map') {
    $PhysicsBackend = 'chaos'
} elseif ($PhysicsBackend -eq 'mujoco') {
    if ($isMeshRig) {
        $mujocoModel = Join-Path (Split-Path -Parent $vehicleInputFile) ("mujoco/$mapId/manifest.json")
        if (-not (Test-Path -LiteralPath $mujocoModel -PathType Leaf)) {
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
    $ue.Project,
    $mapLaunch.WorldUrl,
    '-game',
    '-nosplash',
    '-NoSound'
)
if ($Offscreen) {
    $ueArgs += @('-RenderOffScreen', '-ResX=640', '-ResY=480')
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
    $ueArgs += ('-ViewportHz=' + $ViewportHz.ToString([Globalization.CultureInfo]::InvariantCulture))
}
if ($RenderScale -ne 100) {
    $ueArgs += "-RenderScale=$RenderScale"
}
if ($TrainingMode) { $ueArgs += '-TrainingMode' }

$enableRos = $RosBridge -or $RosControl
if ($enableRos) {
    $ueArgs += '-RosBridge'
    if ($RosControl) {
        $ueArgs += @('-RosControl', '-RosOperatorGyroControl', '-RosOperatorGyroStart', '-RosOperatorGyroStartRate=5.0')
    }
    if ($RosAimControl) { $ueArgs += '-RosAimControl' }
    if ($RosImu) {
        $ueArgs += '-RosImu'
        $mid360Imu = Join-Path (Split-Path -Parent $vehicleInputFile) 'sensors/mid360-imu.json'
        if ($isMeshRig -and (Test-Path -LiteralPath $mid360Imu -PathType Leaf)) {
            $ueArgs += "-ImuProfile=$mid360Imu"
        }
    }
    if ($RosLidar) {
        $ueArgs += '-RosLidar'
        $mid360Lidar = Join-Path (Split-Path -Parent $vehicleInputFile) 'sensors/mid360.json'
        if ($isMeshRig -and (Test-Path -LiteralPath $mid360Lidar -PathType Leaf)) {
            $ueArgs += "-LidarProfile=$mid360Lidar"
        }
    }
    $ueArgs += "-RosPort=$RosPort"
    $ueArgs += "-RosRobotId=$RobotId"
    $ueArgs += ('-RosCameraHz=' + $CameraHz.ToString([Globalization.CultureInfo]::InvariantCulture))
}
foreach ($extra in $mapLaunch.RuntimeArguments) {
    $ueArgs += $extra
}

Write-Output "=== RMSim Pure Simulator Launch ==="
Write-Output "Vehicle  : $vehicleId ($vehicleInputFile)"
Write-Output "Map      : $mapId ($mapKind, spawn=$SpawnId)"
Write-Output "Physics  : $PhysicsBackend $(if($mujocoModel){"[$mujocoModel]"})"
if ($enableRos) {
    Write-Output "ROS2 API : robot_id=$RobotId port=$RosPort domain=$Domain control=$([bool]$RosControl)"
    Write-Output "Topics   : /clock, /$RobotId/cmd_vel, /$RobotId/camera/image_raw, /$RobotId/gimbal/joint_states, /tf"
}

if ($DryRun) {
    Write-Output ('UE Command: ' + $ue.Editor + ' ' + ($ueArgs -join ' '))
    exit 0
}

$bridgeProc = $null
try {
    $ueProc = Start-Process -FilePath $ue.Editor -ArgumentList $ueArgs -PassThru
    Write-Output "Started UE5 Simulator (PID $($ueProc.Id))"

    if ($enableRos -and -not $NoBridgeNode) {
        $bridgeLogDir = Join-Path $root ('artifacts/ros2/simulator-bridge-' + $ueProc.Id)
        New-Item -ItemType Directory -Path $bridgeLogDir -Force | Out-Null
        $bridgeArgs = @('-NoProfile','-File',(Quote-UnrealArgument (Join-Path $root 'tools/environment/Start-RosBridge.ps1')),
            '-RobotId',$RobotId,'-Domain',$Domain,'-Port',$RosPort)
        if ($RosLidar) { $bridgeArgs += '-Lidar' }
        if ($RosAimControl) { $bridgeArgs += '-AimControl' }
        $bridgeProc = Start-Process -FilePath 'powershell.exe' -ArgumentList $bridgeArgs -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $bridgeLogDir 'stdout.log') `
            -RedirectStandardError (Join-Path $bridgeLogDir 'stderr.log')
        Write-Output "Started standalone rmsim_bridge ROS 2 node (PID $($bridgeProc.Id)) for robot_id=$RobotId on port=$RosPort"
        Write-Output "External robot code can now connect via ROS_DOMAIN_ID=$Domain (use_sim_time:=true)."
        Write-Output "Bridge logs: $bridgeLogDir"
        while (-not $ueProc.WaitForExit(500)) {
            if ($bridgeProc.HasExited) {
                Write-Warning "ROS 2 bridge exited (code=$($bridgeProc.ExitCode)). See $bridgeLogDir"
                break
            }
        }
    }

    $ueProc.WaitForExit()
    exit $ueProc.ExitCode
} finally {
    if ($bridgeProc -and -not $bridgeProc.HasExited) {
        Write-Output "Stopping rmsim_bridge (PID $($bridgeProc.Id))..."
        & taskkill.exe /PID $bridgeProc.Id /T /F | Out-Null
    }
}
