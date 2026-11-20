param([ValidateSet('rmuc2027','import_lab')][string]$Scene='rmuc2027',[string]$EngineRoot='E:/simulator-tools/UE_5.8',[string]$OutputDirectory='', [int]$Port=9451, [int]$Domain=71, [switch]$NoRviz,[switch]$Offscreen,[switch]$NoRhi,[switch]$ProfileSensorTiming,[int]$ProfileFrames=0,[switch]$VerifyLidarQueries,[switch]$LidarSerialQuery,[double]$ViewportHz=60,[ValidateScript({$_ -eq 0 -or ($_ -ge 1 -and $_ -le 60)})][double]$CameraHz=0,[switch]$PaceRealTime,[ValidateSet('performance','system')][string]$CpuPolicy='performance',[ValidateSet('simulation','reality')][string]$ControlProfile='simulation',[string]$NavigationSource='',[string]$LocalizationMapYaml='',[ValidateScript({-not [double]::IsNaN($_) -and -not [double]::IsInfinity($_)})][double]$InitialPoseOffsetX=0,[ValidateScript({-not [double]::IsNaN($_) -and -not [double]::IsInfinity($_)})][double]$InitialPoseOffsetY=0,[ValidateScript({-not [double]::IsNaN($_) -and -not [double]::IsInfinity($_)})][double]$InitialPoseOffsetYaw=0,[string]$NavigationFollowProfile='',[string]$LidarProfile='',[ValidateSet(0.001,0.002,0.005,0.01)][double]$SimStep=0.005,[double]$RunSeconds=0,[ValidateSet('chaos','mujoco')][string]$PhysicsBackend='chaos',[string]$MujocoModel='')
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
. (Join-Path $root 'ros2_ws/install_control/local_setup.ps1')
. (Join-Path $root 'ros2_ws/install_lio/local_setup.ps1')
. (Join-Path $root 'ros2_ws/install_navigation/local_setup.ps1')
. (Join-Path $root 'ros2_ws/install_bridge/local_setup.ps1')
$env:PYTHONPATH=(Join-Path $root 'src')+[IO.Path]::PathSeparator+$env:PYTHONPATH
$arguments=@((Join-Path $PSScriptRoot 'navigation_console.py'),'--scene',$Scene,'--engine-root',$EngineRoot,'--port',$Port,'--domain',$Domain)
if($OutputDirectory){$arguments+=@('--output',[IO.Path]::GetFullPath($OutputDirectory))}
$arguments+=@('--physics-backend',$PhysicsBackend)
if($MujocoModel){$arguments+=@('--mujoco-model',[IO.Path]::GetFullPath($MujocoModel))}
if($NoRviz){$arguments+='--no-rviz'}
if($Offscreen){$arguments+='--offscreen'}
if($NoRhi){$arguments+='--no-rhi'}
if($ProfileSensorTiming){$arguments+='--profile-sensor-timing'}
if($ProfileFrames){$arguments+=@('--profile-frames',$ProfileFrames)}
if($VerifyLidarQueries){$arguments+='--verify-lidar-queries'}
if($LidarSerialQuery){$arguments+='--lidar-serial-query'}
if($ViewportHz -gt 0){$arguments+=@('--viewport-hz',$ViewportHz.ToString([Globalization.CultureInfo]::InvariantCulture))}
if($PaceRealTime){$arguments+='--pace-real-time'}
if($NavigationSource){$arguments+=@('--navigation-source',[IO.Path]::GetFullPath($NavigationSource))}
if($LocalizationMapYaml){$arguments+=@('--localization-map-yaml',[IO.Path]::GetFullPath($LocalizationMapYaml))}
$arguments+=@('--cpu-policy',$CpuPolicy)
if($PSBoundParameters.ContainsKey('InitialPoseOffsetX') -or $PSBoundParameters.ContainsKey('InitialPoseOffsetY') -or $PSBoundParameters.ContainsKey('InitialPoseOffsetYaw')){$arguments+=@('--initial-pose-offset',$InitialPoseOffsetX.ToString([Globalization.CultureInfo]::InvariantCulture),$InitialPoseOffsetY.ToString([Globalization.CultureInfo]::InvariantCulture),$InitialPoseOffsetYaw.ToString([Globalization.CultureInfo]::InvariantCulture))}
if($NavigationFollowProfile){$arguments+=@('--navigation-follow-profile',[IO.Path]::GetFullPath($NavigationFollowProfile))}
if($LidarProfile){$arguments+=@('--lidar-profile',[IO.Path]::GetFullPath($LidarProfile))}
$arguments+=@('--camera-hz',$CameraHz.ToString([Globalization.CultureInfo]::InvariantCulture))
$arguments+=@('--control-profile',$ControlProfile,'--sim-step',$SimStep.ToString([Globalization.CultureInfo]::InvariantCulture))
if($RunSeconds -gt 0){$arguments+=@('--run-seconds',$RunSeconds.ToString([Globalization.CultureInfo]::InvariantCulture))}
& python @arguments
exit $LASTEXITCODE
