param(
    [Parameter(Mandatory=$true)][string]$EngineRoot,
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$evidence=[IO.Path]::GetFullPath($EvidenceDirectory)
if(Test-Path -LiteralPath $evidence){throw 'Use a fresh regression evidence directory.'}
New-Item -ItemType Directory -Path $evidence | Out-Null
$profile=Join-Path $root 'assets/vehicles/aim_sentry/drive-profile.json'
$module=Join-Path $root 'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll'
$profileHash=(Get-FileHash -LiteralPath $profile).Hash
$moduleHash=(Get-FileHash -LiteralPath $module).Hash
$map=Get-Content -LiteralPath (Join-Path $root 'assets/maps/import_lab/map.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$map.obstacles=@();$map.PSObject.Properties.Remove('inspection_obstacle_id')
$mapPath=Join-Path $evidence 'empty-map.json'
$map | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $mapPath -Encoding utf8
$cases=@(
    @{script='Test-ManualControls.ps1';path='traction';args=@('-Traction','-TimeoutSeconds','180')},
    @{script='Test-GyroControls.ps1';path='controls';args=@('-TimeoutSeconds','240')},
    @{script='Test-GyroControls.ps1';path='waves';args=@('-WaveRoutes','-PrecisionKeys','-SpinRadS','4','-TimeoutSeconds','240')},
    @{script='Test-ManualControls.ps1';path='integration';args=@('-Contact','-ContactIntegration','-WithTraining','-ContactDistanceCm','150','-ContactSpeedMps','0.5','-TimeoutSeconds','240')},
    @{script='Test-ManualControls.ps1';path='fast';args=@('-Gyro','-SteeringResponse','-ResponseSpeedMps','2','-MapFile',$mapPath,'-TimeoutSeconds','180')},
    @{script='Test-ManualControls.ps1';path='spin';args=@('-Gyro','-SteeringResponse','-ResponseSpeedMps','2','-ResponseSpinRadS','4','-PrecisionTrace','-MapFile',$mapPath,'-TimeoutSeconds','180')},
    @{script='Test-RosBridge.ps1';path='ros';args=@('-RosTopics')},
    @{script='Test-RosBridge.ps1';path='lk';args=@('-LkMotorLoop')}
)
$shell=(Get-Process -Id $PID).Path
$completed=@()
foreach($case in $cases){
    $live=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -eq 'UnrealEditor.exe' -and $_.CommandLine -like '*RMSim.uproject*'})
    if($live.Count){throw 'Another RMSim instance is present; refusing concurrent GPU tests.'}
    if((Get-FileHash -LiteralPath $profile).Hash -ne $profileHash -or (Get-FileHash -LiteralPath $module).Hash -ne $moduleHash){throw 'Frozen regression input changed.'}
    Write-Output ('REGRESSION START '+$case.path)
    $arguments=@('-EngineRoot',$EngineRoot,'-EvidenceDirectory',(Join-Path $evidence $case.path))+$case.args
    & $shell -NoProfile -File (Join-Path $PSScriptRoot $case.script) @arguments
    if($LASTEXITCODE -ne 0){throw ('REGRESSION FAILED '+$case.path)}
    $completed+=@{name=$case.path;script=$case.script;arguments=$arguments;process_exit=0}
    Write-Output ('REGRESSION PASSED '+$case.path)
}
if((Get-FileHash -LiteralPath $profile).Hash -ne $profileHash -or (Get-FileHash -LiteralPath $module).Hash -ne $moduleHash){throw 'Frozen regression input changed after execution.'}
@{status='passed';scope='sequential native control/contact/ROS/LK regression; no OS keyboard or full navigation acceptance';drive_profile_sha256=$profileHash;module_sha256=$moduleHash;completed=$completed} |
    ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidence 'verification.json') -Encoding utf8
