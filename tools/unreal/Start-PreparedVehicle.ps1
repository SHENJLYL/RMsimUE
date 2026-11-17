param(
    [Parameter(Mandatory=$true)][string]$EngineRoot,
    [string]$MapFile='',
    [string]$SpawnId='default',
    [switch]$TrainingMode,
    [switch]$RosBridge,
    [switch]$RosControl,
    [switch]$RosMotorControl,
    [switch]$RosAimControl,
    [switch]$RosImu,
    [string]$ImuProfile='',
    [switch]$RosLidar,
    [string]$LidarProfile='',
    [double]$SimStepSeconds=0,
    [int]$RosPort=9451,
    [switch]$CheckOnly,
    [switch]$AllowMultiple
)
. (Join-Path $PSScriptRoot 'Common.ps1')
$launchMutex=$null;$locked=$false
try {
    $ue=Get-UnrealTools -EngineRoot $EngineRoot
    if(-not $AllowMultiple){
        $launchMutex=[Threading.Mutex]::new($false,'Local\RMSimPreparedLaunch')
        $locked=$launchMutex.WaitOne(0)
        if(-not $locked){Write-Output 'RMSim launch is already in progress. A second instance was not started.';exit 20}
        $project=$ue.Project.Replace('\','/').ToLowerInvariant()
        $running=@(Get-CimInstance Win32_Process -Filter "Name='UnrealEditor.exe'" | Where-Object {
            $_.CommandLine -and $_.CommandLine.Replace('\','/').ToLowerInvariant().Contains($project) -and $_.CommandLine.Contains('-game')
        })
        if($running.Count){
            Write-Output ('RMSim is already running: PID '+(($running | ForEach-Object ProcessId)-join ', '))
            Write-Output 'Use the existing preview, or wait for the active verification to finish. A second instance was not started.'
            exit 20
        }
        $building=@(Get-CimInstance Win32_Process -Filter "Name='dotnet.exe'" | Where-Object {
            $_.CommandLine -and $_.CommandLine.Replace('\','/').ToLowerInvariant().Contains($project) -and $_.CommandLine.ToLowerInvariant().Contains('unrealbuildtool')
        })
        if($building.Count){Write-Output 'RMSim is being built. Wait for the build to finish before starting the preview.';exit 20}
    }
    if($CheckOnly){Write-Output 'No active RMSim preview/build blocks this launch.';exit 0}
    $root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
    if(-not $MapFile){$MapFile=Join-Path $root 'assets/maps/rmuc2027/map.json'}
    $arguments=@('-EngineRoot',$EngineRoot,'-InputType','MeshRig','-InputFile',(Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json'),
        '-MapFile',$MapFile,'-SpawnId',$SpawnId,'-ContactPhysics','-InputEvidence',(Join-Path $root 'artifacts/gameplay/gyro-control/live-input.json'))
    if($TrainingMode){$arguments+='-TrainingMode'}
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
