param([int]$Port=19457,[Parameter(Mandatory=$true)][string]$OutputDirectory,[switch]$WaitReset)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
. (Join-Path $root 'ros2_ws/install_control/local_setup.ps1')
. (Join-Path $root 'ros2_ws/install_bridge/local_setup.ps1')
$arguments=@((Join-Path $PSScriptRoot 'probe_lk_motor_loop.py'),'--port',$Port,'--output',$OutputDirectory)
if($WaitReset){$arguments+='--wait-reset'}
& python @arguments
exit $LASTEXITCODE
