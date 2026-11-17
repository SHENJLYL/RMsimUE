param([Parameter(Mandatory=$true)][string]$OutputDirectory,
      [ValidateSet('committed','unchanged')][string]$Case='committed',[switch]$ExpectHang)
$ErrorActionPreference='Stop'
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
. (Join-Path $repo 'ros2_ws/install_navigation/local_setup.ps1')
. (Join-Path $repo 'ros2_ws/install_bridge/local_setup.ps1')
$arguments=@((Join-Path $PSScriptRoot 'probe_navigation_lifecycle.py'),
 '--manager',(Join-Path $repo 'artifacts/runtime/navigation/windows/lifecycle_manager.exe'),
 '--output',$OutputDirectory,'--case',$Case)
if($ExpectHang){$arguments+='--expect-hang'}
& python @arguments
exit $LASTEXITCODE
