param(
    [Parameter(Mandatory=$true)][string]$EngineRoot,
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
    [int]$Port=19451,
    [switch]$RosTopics,
    [switch]$LkMotorLoop,
    [switch]$AimProbe,
    [switch]$AimWithMotors,
    [switch]$InteractiveReset,
    [switch]$ObserveOnly,
    [switch]$StallProbe,
    [int]$CameraWidth=640,
    [int]$CameraHeight=360,
    [ValidateSet('chaos','mujoco')][string]$PhysicsBackend='chaos',
    [string]$MujocoModel='',
    [switch]$AllowDiagnosticPlane
)
. (Join-Path $PSScriptRoot 'Common.ps1')
$ue=Get-UnrealTools -EngineRoot $EngineRoot
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$evidence=[IO.Path]::GetFullPath($EvidenceDirectory)
if(Test-Path -LiteralPath $evidence){throw 'Use a fresh ROS bridge evidence directory.'}
New-Item -ItemType Directory -Path $evidence | Out-Null
$provenance=[ordered]@{
    started_utc=[DateTime]::UtcNow.ToString('o')
    module_sha256=(Get-FileHash -LiteralPath (Join-Path $root 'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll')).Hash
    rig_sha256=(Get-FileHash -LiteralPath (Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json')).Hash
    drive_profile_sha256=(Get-FileHash -LiteralPath (Join-Path $root 'assets/vehicles/aim_sentry/drive-profile.json')).Hash
    map_sha256=(Get-FileHash -LiteralPath (Join-Path $root 'assets/maps/import_lab/map.json')).Hash
    motor_current_mode=($LkMotorLoop.IsPresent -or $AimWithMotors.IsPresent)
}
$provenance | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'runtime-provenance.json') -Encoding utf8
$arguments=@((Quote-UnrealArgument $ue.Project),'/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode',
    '-game','-RenderOffScreen','-unattended','-NoSplash','-NoSound','-RosControl','-TrainingMode',('-RosPort='+$Port),
    ('-RosCameraWidth='+$CameraWidth),('-RosCameraHeight='+$CameraHeight),'-RosCameraHz=10',
    ('-MeshRigFile='+(Quote-UnrealArgument (Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json'))),
    ('-MapFile='+(Quote-UnrealArgument (Join-Path $root 'assets/maps/import_lab/map.json'))),
    ('-abslog='+(Quote-UnrealArgument (Join-Path $evidence 'runtime.log'))))
if($PhysicsBackend -eq 'mujoco'){
    if(-not $MujocoModel){throw 'MuJoCo bridge test requires a prepared manifest.'}
    $modelPath=(Resolve-Path -LiteralPath $MujocoModel).Path
    $manifest=Get-Content -LiteralPath $modelPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if($manifest.diagnostic_plane_only -and -not $AllowDiagnosticPlane){throw 'Diagnostic plane requires explicit AllowDiagnosticPlane.'}
    $mujocoStep=if($LkMotorLoop -or $AimWithMotors){'-SimStepSeconds=0.001'}else{'-SimStepSeconds=0.005'}
    $arguments+=@('-PhysicsBackend=mujoco',('-MujocoModel='+(Quote-UnrealArgument $modelPath)),$mujocoStep,'-SimRealTimePace')
    if($AllowDiagnosticPlane){$arguments+='-MujocoAllowDiagnosticPlane'}
    $provenance.physics_backend='mujoco'
    $provenance.mujoco_manifest_sha256=(Get-FileHash -LiteralPath $modelPath -Algorithm SHA256).Hash
    $provenance | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'runtime-provenance.json') -Encoding utf8
}
if($ObserveOnly){$arguments=@($arguments | Where-Object {$_ -ne '-RosControl'});$arguments+='-RosBridge'}
if($AimProbe){
    if($RosTopics -or $LkMotorLoop -or $StallProbe){throw 'AimProbe is an independent gate.'}
    if(-not $ObserveOnly){$arguments+='-RosAimControl'}
    if($AimWithMotors){if($ObserveOnly){throw 'AimWithMotors needs control.'};$arguments+='-RosMotorControl'}
}elseif($AimWithMotors){throw 'AimWithMotors needs AimProbe.'}
if($LkMotorLoop){
    if($RosTopics -or $ObserveOnly -or $StallProbe){throw 'LkMotorLoop is an independent gate.'}
    $arguments+='-RosMotorControl'
}
if($StallProbe){if($ObserveOnly -or $RosTopics){throw 'StallProbe uses the native TCP control gate.'};$arguments+='-RosBridgeStallProbe'}
if($InteractiveReset){
    if(-not ($RosTopics -or $LkMotorLoop -or $AimProbe)){throw 'InteractiveReset needs a ROS gate.'}
    $arguments=@($arguments | Where-Object { $_ -notin @('-RenderOffScreen','-unattended') })
    $arguments+=@('-windowed','-ResX=1600','-ResY=1000')
}
$process=$null
try{
    if($InteractiveReset){$process=Start-Process -FilePath $ue.Editor -ArgumentList $arguments -PassThru}
    else{$process=Start-Process -FilePath $ue.Editor -ArgumentList $arguments -WindowStyle Hidden -PassThru}
    [void]$process.Handle;Write-Output "ROS native server PID $($process.Id)"
    if($AimProbe){
        $probeArgs=@((Get-Process -Id $PID).Path,'-NoProfile','-File',
            (Join-Path $root 'tools/integration/Run-AimProbe.ps1'),'-Port',$Port,'-OutputDirectory',(Join-Path $evidence 'aim'))
        if($InteractiveReset){$probeArgs+='-WaitReset'}
        if($ObserveOnly){$probeArgs+='-ObserveOnly'}
        if($AimWithMotors){$probeArgs+='-MotorMode'}
        & (Join-Path $root 'tools/environment/run-env.ps1') -Command $probeArgs
    }elseif($LkMotorLoop){
        $probeArgs=@((Get-Process -Id $PID).Path,'-NoProfile','-File',
            (Join-Path $root 'tools/integration/Run-LkMotorProbe.ps1'),'-Port',$Port,'-OutputDirectory',(Join-Path $evidence 'lk'))
        if($InteractiveReset){$probeArgs+='-WaitReset'}
        & (Join-Path $root 'tools/environment/run-env.ps1') -Command $probeArgs
    }elseif($RosTopics){
        $probeArgs=@((Get-Process -Id $PID).Path,'-NoProfile','-File',(Join-Path $root 'tools/integration/Run-RosTopicsProbe.ps1'),
            '-Port',$Port,'-OutputDirectory',(Join-Path $evidence 'ros'))
        if($InteractiveReset){$probeArgs+='-WaitReset'}
        & (Join-Path $root 'tools/environment/run-env.ps1') -Command $probeArgs
    }else{
        $probeArgs=@((Join-Path $root 'tools/integration/probe_native_bridge.py'),'--port',$Port,'--output',(Join-Path $evidence 'tcp'))
        $probeArgs+=@('--physics-backend',$PhysicsBackend)
        if($ObserveOnly){$probeArgs+='--observe-only'}
        if($StallProbe){$probeArgs+='--stall-probe'}
        & python @probeArgs
    }
    if($LASTEXITCODE -ne 0){throw 'Native bridge client gates failed.'}
}finally{
    if($null -ne $process){
        try{
            if(-not $process.HasExited){
                Stop-Process -Id $process.Id -Force
                if(-not $process.WaitForExit(15000)){throw 'Owned ROS probe server did not finish terminating.'}
            }
        }finally{$process.Dispose()}
    }
}
