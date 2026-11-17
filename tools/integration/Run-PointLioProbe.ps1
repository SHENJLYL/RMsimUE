param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop';$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
. (Join-Path $root 'ros2_ws/install_lio/local_setup.ps1')
& python (Join-Path $root 'tools/integration/probe_pointlio.py') --output $OutputDirectory
exit $LASTEXITCODE
