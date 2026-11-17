param(
 [string]$EngineRoot='E:/simulator-tools/UE_5.8',
 [Parameter(Mandatory=$true)][string]$EvidenceDirectory
)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$out=[IO.Path]::GetFullPath($EvidenceDirectory)
if(Test-Path -LiteralPath $out){throw 'Use a fresh regression evidence directory.'}
New-Item -ItemType Directory -Path $out | Out-Null
$cases=@(
 @{name='mid360';script='Test-Mid360Mount.ps1';flags=@()},
 @{name='manual';script='Test-GyroControls.ps1';flags=@()},
 @{name='ros';script='Test-RosBridge.ps1';flags=@('-RosTopics')},
 @{name='aim';script='Test-RosBridge.ps1';flags=@('-AimProbe')},
 @{name='lidar-lk';script='Test-Lidar.ps1';flags=@('-WithLio','-MotorControl','-MapFile',(Join-Path $root 'assets/maps/rmuc2027/map.json'))}
)
$shell=(Get-Process -Id $PID).Path
$results=@()
foreach($case in $cases){
 $live=@(Get-CimInstance Win32_Process -Filter "Name='UnrealEditor.exe'" | Where-Object {$_.CommandLine -and $_.CommandLine.Replace('\','/').Contains('/unreal/RMSim/RMSim.uproject')})
 if($live.Count){throw 'An RMSim process is already running; refusing concurrent GPU tests.'}
 Write-Output ('SENTRY_REGRESSION_START '+$case.name)
 $arguments=@('-NoProfile','-File',(Join-Path $PSScriptRoot $case.script),'-EngineRoot',$EngineRoot,'-EvidenceDirectory',(Join-Path $out $case.name))+$case.flags
 & $shell @arguments
 $code=$LASTEXITCODE
 $results+=@{name=$case.name;process_exit=$code;passed=($code -eq 0)}
 @{status=if($code -eq 0){'running'}else{'failed'};runs=$results} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $out 'verification.json') -Encoding UTF8
 if($code -ne 0){throw ('Regression failed: '+$case.name)}
 Write-Output ('SENTRY_REGRESSION_PASS '+$case.name)
}
@{status='passed';runs=$results;scope='sequential actual native engine tests; individual results retain their own checks and hashes'} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $out 'verification.json') -Encoding UTF8
