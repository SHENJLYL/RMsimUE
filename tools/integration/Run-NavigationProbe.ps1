param([int]$Port=19501,[Parameter(Mandatory=$true)][string]$OutputDirectory,[switch]$LocalizationOnly,[switch]$MismatchedMap,[switch]$PublicClient,[switch]$WaitReset,
 [double]$GoalX=5.5,[double]$GoalY=1,[double]$InitialX=.6,[double]$InitialY=-.4,[double]$InitialYaw=.15,[switch]$RoundTrip,[double]$ReturnX=0,[double]$ReturnY=0)
$ErrorActionPreference='Stop';$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
. (Join-Path $root 'ros2_ws/install_lio/local_setup.ps1')
. (Join-Path $root 'ros2_ws/install_navigation/local_setup.ps1')
. (Join-Path $root 'ros2_ws/install_bridge/local_setup.ps1')
$arguments=@((Join-Path $root 'tools/integration/probe_navigation.py'),'--port',$Port,'--output',$OutputDirectory)
$culture=[Globalization.CultureInfo]::InvariantCulture
$arguments+=@('--goal-x',$GoalX.ToString($culture),'--goal-y',$GoalY.ToString($culture),'--initial-x',$InitialX.ToString($culture),'--initial-y',$InitialY.ToString($culture),'--initial-yaw',$InitialYaw.ToString($culture))
if($RoundTrip){$arguments+=@('--round-trip','--return-x',$ReturnX.ToString($culture),'--return-y',$ReturnY.ToString($culture))}
if($LocalizationOnly){$arguments+='--localization-only'}
if($MismatchedMap){$arguments+='--mismatched-map'}
if($PublicClient){$arguments+='--public-client'}
if($WaitReset){$arguments+='--wait-reset'}
& python @arguments
exit $LASTEXITCODE
