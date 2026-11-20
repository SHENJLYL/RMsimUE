param([ValidateSet('rmuc2027','import_lab')][string]$Scene='rmuc2027',[string]$EngineRoot='E:/simulator-tools/UE_5.8',[string]$OutputDirectory='', [int]$Port=9451, [int]$Domain=71,[switch]$ReplaceManualPreview,[switch]$NoRviz,[switch]$Offscreen,[switch]$NoRhi,[switch]$ProfileSensorTiming,[switch]$ProfileMujocoTiming,[int]$ProfileFrames=0,[switch]$VerifyLidarQueries,[switch]$LidarSerialQuery,[double]$ViewportHz=60,[ValidateScript({$_ -eq 0 -or ($_ -ge 1 -and $_ -le 60)})][double]$CameraHz=0,[switch]$PaceRealTime,[ValidateSet('performance','system')][string]$CpuPolicy='performance',[ValidateSet('simulation','reality')][string]$ControlProfile='simulation',[string]$NavigationSource='',[string]$LocalizationMapYaml='',[ValidateScript({-not [double]::IsNaN($_) -and -not [double]::IsInfinity($_)})][double]$InitialPoseOffsetX=0,[ValidateScript({-not [double]::IsNaN($_) -and -not [double]::IsInfinity($_)})][double]$InitialPoseOffsetY=0,[ValidateScript({-not [double]::IsNaN($_) -and -not [double]::IsInfinity($_)})][double]$InitialPoseOffsetYaw=0,[string]$NavigationFollowProfile='',[string]$LidarProfile='',[ValidateSet(0.001,0.002,0.005,0.01)][double]$SimStep=0.005,[double]$RunSeconds=0,[ValidateSet('chaos','mujoco')][string]$PhysicsBackend='mujoco',[string]$MujocoModel='')
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$mutex=[Threading.Mutex]::new($false,'Local\RMSimNavigationConsole')
$locked=$false
try {
 $locked=$mutex.WaitOne(0)
 if(-not $locked){throw 'A navigation console is already running. Use the existing window.'}
 $project=(Join-Path $root 'unreal/RMSim/RMSim.uproject').Replace('\','/').ToLowerInvariant()
 $existing=@(Get-CimInstance Win32_Process -Filter "Name='UnrealEditor.exe'" | Where-Object {$_.CommandLine -and $_.CommandLine.Replace('\','/').ToLowerInvariant().Contains($project)})
 foreach($item in $existing){
  if(-not $ReplaceManualPreview -or $item.CommandLine -notmatch ' -game ' -or $item.CommandLine -match 'RosControl|RosMotorControl|Probe|RosLidar|RosImu'){
   throw ('An RMSim editor, navigation or test process is already running: '+$item.ProcessId)
  }
  Write-Output ('Switching manual RMSim preview '+$item.ProcessId+' to navigation mode.')
  $process=Get-Process -Id $item.ProcessId -ErrorAction Stop
  [void]$process.CloseMainWindow()
  if(-not $process.WaitForExit(15000)){throw 'Manual preview did not close; no new GPU instance was started.'}
  $process.Dispose()
 }
 $arguments=@('powershell','-NoProfile','-File',(Join-Path $root 'tools/integration/Run-NavigationConsole.ps1'),'-Scene',$Scene,'-EngineRoot',$EngineRoot,'-Port',$Port,'-Domain',$Domain)
 if($OutputDirectory){$arguments+=@('-OutputDirectory',[IO.Path]::GetFullPath($OutputDirectory))}
 $arguments+=@('-PhysicsBackend',$PhysicsBackend)
 if($MujocoModel){$arguments+=@('-MujocoModel',[IO.Path]::GetFullPath($MujocoModel))}
 if($NoRviz){$arguments+='-NoRviz'}
 if($Offscreen){$arguments+='-Offscreen'}
 if($NoRhi){$arguments+='-NoRhi'}
 if($ProfileSensorTiming){$arguments+='-ProfileSensorTiming'}
 if($ProfileMujocoTiming){$arguments+='-ProfileMujocoTiming'}
 if($ProfileFrames){$arguments+=@('-ProfileFrames',$ProfileFrames)}
 if($VerifyLidarQueries){$arguments+='-VerifyLidarQueries'}
 if($LidarSerialQuery){$arguments+='-LidarSerialQuery'}
 if($ViewportHz -gt 0){$arguments+=@('-ViewportHz',$ViewportHz.ToString([Globalization.CultureInfo]::InvariantCulture))}
 if($PaceRealTime){$arguments+='-PaceRealTime'}
 if($NavigationSource){$arguments+=@('-NavigationSource',[IO.Path]::GetFullPath($NavigationSource))}
 if($LocalizationMapYaml){$arguments+=@('-LocalizationMapYaml',[IO.Path]::GetFullPath($LocalizationMapYaml))}
 $arguments+=@('-CpuPolicy',$CpuPolicy)
if($PSBoundParameters.ContainsKey('InitialPoseOffsetX') -or $PSBoundParameters.ContainsKey('InitialPoseOffsetY') -or $PSBoundParameters.ContainsKey('InitialPoseOffsetYaw')){$arguments+=@('-InitialPoseOffsetX',$InitialPoseOffsetX.ToString([Globalization.CultureInfo]::InvariantCulture),'-InitialPoseOffsetY',$InitialPoseOffsetY.ToString([Globalization.CultureInfo]::InvariantCulture),'-InitialPoseOffsetYaw',$InitialPoseOffsetYaw.ToString([Globalization.CultureInfo]::InvariantCulture))}
 if($NavigationFollowProfile){$arguments+=@('-NavigationFollowProfile',[IO.Path]::GetFullPath($NavigationFollowProfile))}
 if($LidarProfile){$arguments+=@('-LidarProfile',[IO.Path]::GetFullPath($LidarProfile))}
 $arguments+=@('-CameraHz',$CameraHz.ToString([Globalization.CultureInfo]::InvariantCulture))
$arguments+=@('-ControlProfile',$ControlProfile,'-SimStep',$SimStep.ToString([Globalization.CultureInfo]::InvariantCulture))
 if($RunSeconds -gt 0){$arguments+=@('-RunSeconds',$RunSeconds.ToString([Globalization.CultureInfo]::InvariantCulture))}
 & (Join-Path $PSScriptRoot 'run-env.ps1') -Command $arguments
 exit $LASTEXITCODE
} catch {
 [Console]::Error.WriteLine($_.Exception.Message)
 exit 3
} finally {
 if($locked){$mutex.ReleaseMutex()}
 $mutex.Dispose()
}
