param([Parameter(Mandatory=$true)][string]$OutputDirectory,[switch]$Planar)
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
. (Join-Path $repo 'ros2_ws/install_navigation/local_setup.ps1')
. (Join-Path $repo 'ros2_ws/install_bridge/local_setup.ps1')
$arguments=@((Join-Path $PSScriptRoot 'probe_navigation_frames.py'),
 '--program',(Join-Path $repo 'artifacts/runtime/navigation/windows/fake_vel_transform_node.exe'),
 '--output',$OutputDirectory)
if($Planar){$arguments+='--planar'}
& python @arguments
exit $LASTEXITCODE
