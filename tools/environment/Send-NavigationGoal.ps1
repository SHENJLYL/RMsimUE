param([Parameter(Mandatory=$true)][double]$X,[Parameter(Mandatory=$true)][double]$Y)
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'));$culture=[Globalization.CultureInfo]::InvariantCulture
& (Join-Path $PSScriptRoot 'run-env.ps1') -Command @('python',(Join-Path $root 'tools/integration/send_navigation_goal.py'),$X.ToString($culture),$Y.ToString($culture))
exit $LASTEXITCODE
