param([int]$Port=19481,[string]$OutputDirectory='', [switch]$WithLio,[switch]$SerializationOnly,[switch]$WaitReset,[switch]$MotorControl)
$ErrorActionPreference='Stop';$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
. (Join-Path $root 'ros2_ws/install_lio/local_setup.ps1')
. (Join-Path $root 'ros2_ws/install_bridge/local_setup.ps1')
if($MotorControl){. (Join-Path $root 'ros2_ws/install_control/local_setup.ps1')}
& python (Join-Path $root 'tools/integration/probe_livox_serialization.py')
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
if($SerializationOnly){exit 0}
$arguments=@((Join-Path $root 'tools/integration/probe_lidar.py'),'--port',$Port,'--output',$OutputDirectory)
if($WithLio){$arguments+='--with-lio'}
if($WaitReset){$arguments+='--wait-reset'}
if($MotorControl){$arguments+='--motor-control'}
& python @arguments
exit $LASTEXITCODE
