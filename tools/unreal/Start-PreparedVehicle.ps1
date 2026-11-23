param(
    [Parameter(Mandatory=$true)][string]$EngineRoot,
    [string]$MapFile='',
    [string]$SpawnId='default',
    [ValidateSet('Default','D3D11','D3D12')][string]$GraphicsRhi='Default',
    [switch]$SingleShaderWorker,
    [switch]$TrainingMode,
    [switch]$RosBridge,
    [switch]$RosControl,
    [switch]$RosMotorControl,
    [switch]$RosAimControl,
    [switch]$RosImu,
    [string]$ImuProfile='',
    [switch]$RosLidar,
    [string]$LidarProfile='',
    [ValidateSet('chaos','mujoco')][string]$PhysicsBackend='chaos',
    [string]$MujocoModel='',
    [switch]$SimRealTimePace,
    [ValidateScript({$_ -eq 0 -or ($_ -ge 10 -and $_ -le 144)})][double]$ViewportHz=0,
    [ValidateRange(50,100)][int]$RenderScale=100,
    [double]$SimStepSeconds=0,
    [int]$RosPort=9451,
    [switch]$CheckOnly,
    [switch]$AllowMultiple
)
. (Join-Path $PSScriptRoot 'Common.ps1')
. (Join-Path $PSScriptRoot 'SceneMap.ps1')
$launchMutex=$null;$locked=$false
try {
    $ue=Get-UnrealTools -EngineRoot $EngineRoot
    $project=[IO.Path]::GetFullPath($ue.Project).Replace('\','/').ToLowerInvariant()
    $hasher=[Security.Cryptography.SHA256]::Create()
    try { $projectHash=([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($project)))).Replace('-','').ToLowerInvariant() }
    finally { $hasher.Dispose() }
    # Shared with Import-WTScene: each worktree has its own project lock.
    $launchMutex=[Threading.Mutex]::new($false,('Local\RMSimSceneImport_'+$projectHash))
    try { $locked=$launchMutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $locked=$true }
    if(-not $locked){Write-Output 'A launch or scene import is already active for this project.';exit 20}
    $projectPattern='(?:^|[\s"=])'+[regex]::Escape($project)+'(?=$|[\s"])'
    $active=@(Get-CimInstance Win32_Process -Filter "Name='UnrealEditor.exe' OR Name='UnrealEditor-Cmd.exe' OR Name='dotnet.exe' OR Name='UnrealBuildTool.exe' OR Name='cmd.exe'" | Where-Object {
        if(-not $_.CommandLine){return $false}
        $command=$_.CommandLine.Replace('\','/').ToLowerInvariant()
        if($command -notmatch $projectPattern){return $false}
        $editor=$_.Name -eq 'UnrealEditor.exe'
        $commandlet=$_.Name -eq 'UnrealEditor-Cmd.exe'
        $game=$editor -and $command -match '(?:^|[\s"])-game(?=$|[\s"])'
        $build=$_.Name -eq 'UnrealBuildTool.exe' -or $command.Contains('unrealbuildtool') -or $command.Contains('/build.bat')
        $commandlet -or $build -or ($editor -and -not ($AllowMultiple -and $game))
    })
    if($active.Count){
        Write-Output ('RMSim project has an active editor, import or build: PID '+(($active | ForEach-Object ProcessId)-join ', '))
        Write-Output 'Wait for that project session to finish before starting this preview.'
        exit 20
    }
    $root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
    if(-not $MapFile){$MapFile=Join-Path $root 'assets/maps/rmuc2027/map.json'}
    $mapLaunch=Get-RMSimMapLaunch -MapFile $MapFile -Project $ue.Project -SpawnId $SpawnId
    $MapFile=$mapLaunch.MapFile
    if($PhysicsBackend -eq 'mujoco') {
        if (-not $MujocoModel) { $MujocoModel=Join-Path $root 'assets/vehicles/aim_sentry/mujoco/rmuc2027/manifest.json' }
        if (-not (Test-Path -LiteralPath $MujocoModel -PathType Leaf)) { throw 'Prepare the MuJoCo model using tools/mujoco/prepare_model.py.' }
        $sdkRuntime=Join-Path $root 'third_party/mujoco-sdk/4.0.1-rmsim-static-world-flex.3/windows-x86_64/bin/mujoco.dll'
        $stagedRuntime=Join-Path (Split-Path -Parent $ue.Project) 'Binaries/Win64/mujoco.dll'
        if (-not (Test-Path -LiteralPath $sdkRuntime -PathType Leaf)) { throw 'Build the pinned native runtime using tools/mujoco/Build-MujocoRuntime.ps1.' }
        if (-not (Test-Path -LiteralPath $stagedRuntime -PathType Leaf) -or
            (Get-FileHash -LiteralPath $sdkRuntime -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $stagedRuntime -Algorithm SHA256).Hash) {
            throw 'The staged MuJoCo runtime differs from the pinned SDK. Rebuild RMSim before launching.'
        }
        $verifyPython=Join-Path $root 'artifacts/environments/mujoco-4.0.1/Scripts/python.exe'
        if (-not (Test-Path -LiteralPath $verifyPython)) { $verifyPython=(Get-Command python -ErrorAction Stop).Source }
        $verifyOutput=Join-Path $root ('artifacts/verification/mujoco-launch/'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8))
        & $verifyPython (Join-Path $root 'tools/mujoco/verify_model.py') --manifest $MujocoModel --map-file $MapFile --preflight-only --output $verifyOutput | Out-Null
        if ($LASTEXITCODE -ne 0) { throw ('MuJoCo physical input verification failed: '+(Join-Path $verifyOutput 'report.json')) }
        Write-Output ('MuJoCo source/model/runtime preflight passed: '+$verifyOutput)
    }
    if($CheckOnly){
        $runtimeArgumentsJson=ConvertTo-Json -InputObject @($mapLaunch.RuntimeArguments) -Compress
        Write-Output ('Launch preflight passed: '+$mapLaunch.WorldUrl+' GraphicsRhi='+$GraphicsRhi+' SingleShaderWorker='+[bool]$SingleShaderWorker+' RuntimeArguments='+$runtimeArgumentsJson)
        exit 0
    }
    $arguments=@('-EngineRoot',$EngineRoot,'-InputType','MeshRig','-InputFile',(Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json'),
        '-MapFile',$MapFile,'-SpawnId',$SpawnId,'-GraphicsRhi',$GraphicsRhi,'-ContactPhysics','-InputEvidence',(Join-Path $root 'artifacts/gameplay/gyro-control/live-input.json'))
    $arguments+=@('-PhysicsBackend',$PhysicsBackend)
    if($MujocoModel){$arguments+=@('-MujocoModel',$MujocoModel)}
    if($SimRealTimePace){$arguments+='-SimRealTimePace'}
    if($ViewportHz -gt 0){$arguments+=@('-ViewportHz',$ViewportHz.ToString([Globalization.CultureInfo]::InvariantCulture))}
    if($RenderScale -ne 100){$arguments+=@('-RenderScale',$RenderScale)}
    if($TrainingMode){$arguments+='-TrainingMode'}
    if($SingleShaderWorker){$arguments+='-SingleShaderWorker'}
    if($RosBridge -or $RosControl -or $RosMotorControl -or $RosAimControl -or $RosImu -or $ImuProfile -or $RosLidar -or $LidarProfile){
        if($RosPort -lt 1024 -or $RosPort -gt 65535){throw 'ROS port must be 1024..65535.'}
        $arguments+=@('-RosBridge','-RosPort',$RosPort)
    }
    if($RosControl){$arguments+='-RosControl'}
    if($RosMotorControl){$arguments+='-RosMotorControl'}
    if($RosAimControl){$arguments+='-RosAimControl'}
    if($RosImu -or $ImuProfile){$arguments+='-RosImu'}
    if($ImuProfile){$arguments+=@('-ImuProfile',$ImuProfile)}
    if($RosLidar -or $LidarProfile){$arguments+='-RosLidar'}
    if($LidarProfile){$arguments+=@('-LidarProfile',$LidarProfile)}
    if($SimStepSeconds -ne 0){$arguments+=@('-SimStepSeconds',$SimStepSeconds.ToString([Globalization.CultureInfo]::InvariantCulture))}
    & (Get-Process -Id $PID).Path -NoProfile -File (Join-Path $PSScriptRoot 'Run-ImportLab.ps1') @arguments
    exit $LASTEXITCODE
}catch{
    [Console]::Error.WriteLine($_.Exception.Message);exit 3
}finally{
    if($locked){$launchMutex.ReleaseMutex()}
    if($null -ne $launchMutex){$launchMutex.Dispose()}
}
