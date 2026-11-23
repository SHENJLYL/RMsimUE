param([Parameter(Mandatory=$true)][string]$OutputDirectory,[switch]$CaptureLocalizationClouds)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
foreach($overlay in @('install_control','install_lio','install_navigation','install_bridge')){
 . (Join-Path $root ('ros2_ws/'+$overlay+'/local_setup.ps1'))
}
$env:PYTHONPATH=(Join-Path $root 'src')+[IO.Path]::PathSeparator+$env:PYTHONPATH
$probeArgs=@('--output',([IO.Path]::GetFullPath($OutputDirectory)))
if($CaptureLocalizationClouds){$probeArgs+='--capture-localization-clouds'}
& python (Join-Path $PSScriptRoot 'probe_high_speed_diagnostics.py') @probeArgs
exit $LASTEXITCODE
