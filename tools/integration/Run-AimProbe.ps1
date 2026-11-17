param([int]$Port=19461,[Parameter(Mandatory=$true)][string]$OutputDirectory,[switch]$WaitReset,[switch]$ObserveOnly,[switch]$MotorMode)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
. (Join-Path $root 'ros2_ws/install_control/local_setup.ps1')
. (Join-Path $root 'ros2_ws/install_bridge/local_setup.ps1')
$arguments=@((Join-Path $PSScriptRoot 'probe_ros_aim.py'),'--port',$Port,'--output',$OutputDirectory)
if($WaitReset){$arguments+='--wait-reset'}
if($ObserveOnly){$arguments+='--observe-only'}
if($MotorMode){$arguments+='--motor-mode'}
& python @arguments
exit $LASTEXITCODE
