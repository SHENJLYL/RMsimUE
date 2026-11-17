param([string]$MapYaml='',[string]$RobotId='aim_sentry',[double]$InitialX=.6,[double]$InitialY=-.4,[double]$InitialYaw=.15)
$ErrorActionPreference='Stop';$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
if(-not $MapYaml){$MapYaml=Join-Path $root 'assets/maps/import_lab/navigation/map.yaml'}
$culture=[Globalization.CultureInfo]::InvariantCulture
& (Join-Path $PSScriptRoot 'run-env.ps1') -Command @('powershell','-NoProfile','-File',(Join-Path $root 'tools/integration/Run-Navigation.ps1'),
 '-MapYaml',$MapYaml,'-RobotId',$RobotId,'-InitialX',$InitialX.ToString($culture),'-InitialY',$InitialY.ToString($culture),'-InitialYaw',$InitialYaw.ToString($culture))
exit $LASTEXITCODE
