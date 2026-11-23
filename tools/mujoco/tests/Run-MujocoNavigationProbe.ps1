param(
 [Parameter(Mandatory=$true)][string]$SessionDirectory,
 [Parameter(Mandatory=$true)][string]$OutputDirectory,
 [int]$Domain=174
)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
foreach($overlay in @('install_control','install_lio','install_navigation','install_bridge')){
 . (Join-Path $root ('ros2_ws/'+$overlay+'/local_setup.ps1'))
}
$env:PYTHONPATH=(Join-Path $root 'src')+[IO.Path]::PathSeparator+$env:PYTHONPATH
& python (Join-Path $PSScriptRoot 'probe_mujoco_navigation.py') --session ([IO.Path]::GetFullPath($SessionDirectory)) --output ([IO.Path]::GetFullPath($OutputDirectory)) --domain $Domain
exit $LASTEXITCODE
