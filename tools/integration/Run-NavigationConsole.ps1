param([ValidateSet('rmuc2027','import_lab')][string]$Scene='rmuc2027',[string]$EngineRoot='E:/simulator-tools/UE_5.8',[string]$OutputDirectory='', [int]$Port=9451, [int]$Domain=71, [switch]$NoRviz,[switch]$Offscreen,[ValidateSet('simulation','reality')][string]$ControlProfile='simulation',[string]$NavigationSource='',[string]$NavigationFollowProfile='',[ValidateSet(0.001,0.002,0.005,0.01)][double]$SimStep=0.005,[double]$RunSeconds=0)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
. (Join-Path $root 'ros2_ws/install_control/local_setup.ps1')
. (Join-Path $root 'ros2_ws/install_lio/local_setup.ps1')
. (Join-Path $root 'ros2_ws/install_navigation/local_setup.ps1')
. (Join-Path $root 'ros2_ws/install_bridge/local_setup.ps1')
$env:PYTHONPATH=(Join-Path $root 'src')+[IO.Path]::PathSeparator+$env:PYTHONPATH
$arguments=@((Join-Path $PSScriptRoot 'navigation_console.py'),'--scene',$Scene,'--engine-root',$EngineRoot,'--port',$Port,'--domain',$Domain)
if($OutputDirectory){$arguments+=@('--output',[IO.Path]::GetFullPath($OutputDirectory))}
if($NoRviz){$arguments+='--no-rviz'}
if($Offscreen){$arguments+='--offscreen'}
if($NavigationSource){$arguments+=@('--navigation-source',[IO.Path]::GetFullPath($NavigationSource))}
if($NavigationFollowProfile){$arguments+=@('--navigation-follow-profile',[IO.Path]::GetFullPath($NavigationFollowProfile))}
$arguments+=@('--control-profile',$ControlProfile,'--sim-step',$SimStep.ToString([Globalization.CultureInfo]::InvariantCulture))
if($RunSeconds -gt 0){$arguments+=@('--run-seconds',$RunSeconds.ToString([Globalization.CultureInfo]::InvariantCulture))}
& python @arguments
exit $LASTEXITCODE
