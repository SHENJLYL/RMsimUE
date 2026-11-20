param([Parameter(Mandatory=$true)][int]$Port,[Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
. (Join-Path $root 'ros2_ws/install_lio/local_setup.ps1')
. (Join-Path $root 'ros2_ws/install_bridge/local_setup.ps1')
& python (Join-Path $PSScriptRoot 'probe_mujoco_ros_topics.py') --port $Port --output $OutputDirectory
exit $LASTEXITCODE
