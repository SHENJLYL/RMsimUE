param(
 [string]$SessionDirectory='',
 [Parameter(Mandatory=$true)][string]$OutputDirectory,
 [switch]$FollowOnly,
 [switch]$NativeActions,
 [double]$Seconds=600,
 [double]$GoalX=9.45,
 [double]$GoalY=6.4,
 [switch]$SkipSingle
)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
foreach($overlay in @('install_control','install_lio','install_navigation','install_bridge')){
 . (Join-Path $root ('ros2_ws/'+$overlay+'/local_setup.ps1'))
}
$env:PYTHONPATH=(Join-Path $root 'src')+[IO.Path]::PathSeparator+$env:PYTHONPATH
$out=[IO.Path]::GetFullPath($OutputDirectory)
if($NativeActions){
 $arguments=@((Join-Path $PSScriptRoot 'probe_sentry_navigation.py'),'--output',$out)
}elseif($FollowOnly){
 if($Seconds -le 0){throw 'Observer duration must be positive.'}
 $arguments=@((Join-Path $PSScriptRoot 'observe_navigation_follow.py'),'--output',$out,'--duration',$Seconds.ToString([Globalization.CultureInfo]::InvariantCulture))
}else{
 if(-not $SessionDirectory){throw 'The GUI acceptance requires its owned session directory.'}
 $session=(Resolve-Path -LiteralPath $SessionDirectory).Path
 $culture=[Globalization.CultureInfo]::InvariantCulture
 $arguments=@((Join-Path $PSScriptRoot 'test_navigation_console_ui.py'),'--session',$session,'--output',$out,'--goal-x',$GoalX.ToString($culture),'--goal-y',$GoalY.ToString($culture))
 if($SkipSingle){$arguments+='--skip-single'}
}
& python @arguments
exit $LASTEXITCODE
