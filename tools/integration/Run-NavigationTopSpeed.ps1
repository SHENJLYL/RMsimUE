<#
.SYNOPSIS
Validate the current source speed on the physical import_lab lane.
.DESCRIPTION
The probe sends an original Nav2 staging goal to (-8.5, 0), requires actual
arrival within 0.25 m and a stationary window, then measures the 17 m goal
to (8.5, 0). Source speed/acceleration, collision and acceptance thresholds
come from the unchanged probe/source contracts. No pose reset is performed.
.PARAMETER ValidateOnly
Check both declared collision lanes and source-derived acceleration distance
without starting ROS or sending either navigation action.
#>
param(
 [Parameter(Mandatory=$true)][string]$SessionDirectory,
 [Parameter(Mandatory=$true)][string]$OutputDirectory,
 [int]$Domain=71,
 [switch]$ValidateOnly
)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
foreach($overlay in @('install_control','install_lio','install_navigation','install_bridge')){
 . (Join-Path $root ('ros2_ws/'+$overlay+'/local_setup.ps1'))
}
$env:PYTHONPATH=(Join-Path $root 'src')+[IO.Path]::PathSeparator+$env:PYTHONPATH
$session=(Resolve-Path -LiteralPath $SessionDirectory).Path
$out=[IO.Path]::GetFullPath($OutputDirectory)
$arguments=@((Join-Path $PSScriptRoot 'probe_navigation_top_speed.py'),
 '--session',$session,'--output',$out,'--domain',$Domain.ToString([Globalization.CultureInfo]::InvariantCulture))
if($ValidateOnly){$arguments+='--validate-only'}
& python @arguments
exit $LASTEXITCODE
