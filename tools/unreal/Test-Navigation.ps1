param([Parameter(Mandatory=$true)][string]$EngineRoot,[Parameter(Mandatory=$true)][string]$EvidenceDirectory,[int]$Port=19501,[switch]$LocalizationOnly,[switch]$MismatchedMap,[switch]$PublicClient,[switch]$InteractiveReset,[switch]$RecoverSession,
 [string]$MapFile='',[string]$NavigationMap='',[double]$GoalX=5.5,[double]$GoalY=1,[double]$InitialX=.6,[double]$InitialY=-.4,[double]$InitialYaw=.15,[switch]$RoundTrip,[double]$ReturnX=0,[double]$ReturnY=0)
. (Join-Path $PSScriptRoot 'Common.ps1')
$ue=Get-UnrealTools -EngineRoot $EngineRoot;$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'));$out=[IO.Path]::GetFullPath($EvidenceDirectory)
if(Test-Path -LiteralPath $out){throw 'Use a fresh navigation evidence directory.'}
New-Item -ItemType Directory -Path $out|Out-Null
if(-not $MapFile){$MapFile=Join-Path $root 'assets/maps/import_lab/map.json'}
Copy-Item -LiteralPath $MapFile -Destination (Join-Path $out 'map.json')
$env:PYTHONPATH=Join-Path $root 'src'
if($NavigationMap){Copy-Item -LiteralPath $NavigationMap -Destination (Join-Path $out 'map') -Recurse}
else{& python -m rm_map_tools export (Join-Path $out 'map.json') --output (Join-Path $out 'map') --resolution .05;if($LASTEXITCODE -ne 0){throw 'Map export failed'}}
if($MismatchedMap){$metadata=Join-Path $out 'map/metadata.json';$data=Get-Content -LiteralPath $metadata -Raw -Encoding UTF8|ConvertFrom-Json;$data.source_md5='00000000000000000000000000000000';$data|ConvertTo-Json -Depth 20|Set-Content -LiteralPath $metadata -Encoding utf8}
@{started_utc=[DateTime]::UtcNow.ToString('o');
 module_sha256=(Get-FileHash -LiteralPath (Join-Path $root 'unreal/RMSim/Binaries/Win64/UnrealEditor-RMSim.dll')).Hash;
 rig_sha256=(Get-FileHash -LiteralPath (Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json')).Hash;
 drive_profile_sha256=(Get-FileHash -LiteralPath (Join-Path $root 'assets/vehicles/aim_sentry/drive-profile.json')).Hash;
 map_sha256=(Get-FileHash -LiteralPath (Join-Path $out 'map.json')).Hash;
 navigation_map_sha256=(Get-FileHash -LiteralPath (Join-Path $out 'map/map.pgm')).Hash} |
 ConvertTo-Json | Set-Content -LiteralPath (Join-Path $out 'runtime-provenance.json') -Encoding utf8
$arguments=@((Quote-UnrealArgument $ue.Project),'/Engine/Maps/Entry?game=/Script/RMSim.ImportLabGameMode','-game','-RenderOffScreen','-unattended','-NoSplash','-NoSound',
 '-RosControl','-RosImu','-RosLidar','-SimStepSeconds=0.001',('-RosPort='+$Port),'-RosCameraWidth=320','-RosCameraHeight=240',
 ('-MeshRigFile='+(Quote-UnrealArgument (Join-Path $root 'assets/vehicles/aim_sentry/ue_mesh_rig.json'))),
 ('-MapFile='+(Quote-UnrealArgument (Join-Path $out 'map.json'))),('-abslog='+(Quote-UnrealArgument (Join-Path $out 'runtime.log'))))
if($InteractiveReset){$arguments=@($arguments|Where-Object {$_ -notin @('-RenderOffScreen','-unattended')});$arguments+=@('-windowed','-ResX=1600','-ResY=1000');$process=Start-Process -FilePath $ue.Editor -ArgumentList $arguments -PassThru}
else{$process=Start-Process -FilePath $ue.Editor -ArgumentList $arguments -WindowStyle Hidden -PassThru}
[void]$process.Handle
try{
 Write-Output ('Navigation server PID '+$process.Id)
 $client=@((Get-Process -Id $PID).Path,'-NoProfile','-File',(Join-Path $root 'tools/integration/Run-NavigationProbe.ps1'),'-Port',$Port,'-OutputDirectory',(Join-Path $out 'ros'))
 $culture=[Globalization.CultureInfo]::InvariantCulture
 $client+=@('-GoalX',$GoalX.ToString($culture),'-GoalY',$GoalY.ToString($culture),'-InitialX',$InitialX.ToString($culture),'-InitialY',$InitialY.ToString($culture),'-InitialYaw',$InitialYaw.ToString($culture))
 if($RoundTrip){$client+=@('-RoundTrip','-ReturnX',$ReturnX.ToString($culture),'-ReturnY',$ReturnY.ToString($culture))}
 if($LocalizationOnly){$client+='-LocalizationOnly'}
 if($MismatchedMap){$client+='-MismatchedMap'}
 if($PublicClient){$client+='-PublicClient'}
 if($InteractiveReset){$client+='-WaitReset'}
 if($RecoverSession){$client+='-RecoverSession'}
 & (Join-Path $root 'tools/environment/run-env.ps1') -Command $client
 if($LASTEXITCODE -ne 0){throw 'Native navigation gate failed.'}
}finally{
 if($process){try{if(-not $process.HasExited){Stop-Process -Id $process.Id -Force;if(-not $process.WaitForExit(15000)){throw 'Owned test server did not finish terminating.'}}}finally{$process.Dispose()}}
}
