param([int]$Port=19471,[Parameter(Mandatory=$true)][string]$OutputDirectory,[string]$Fixture='motion',[switch]$WaitReset)
$ErrorActionPreference='Stop';$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
. (Join-Path $root 'ros2_ws/install_bridge/local_setup.ps1')
$argsList=@((Join-Path $root 'tools/integration/probe_imu.py'),'--port',$Port,'--output',$OutputDirectory,'--fixture',$Fixture)
if($WaitReset){$argsList+='--wait-reset'}
& python @argsList
exit $LASTEXITCODE
