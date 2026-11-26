param([int]$Port=9451,[switch]$MotorControl,[switch]$AimControl,[switch]$Lidar,
      [string]$RobotId='aim_sentry',[ValidateRange(0,232)][int]$Domain,[string]$NavigationFollowProfile='')
if($Port -lt 1024 -or $Port -gt 65535){throw 'Port must be 1024..65535.'}
$shell=(Get-Process -Id $PID).Path
$arguments=@($shell,'-NoProfile','-File',(Join-Path $PSScriptRoot '../integration/Run-InstalledBridge.ps1'),'-Port',$Port,'-RobotId',$RobotId)
if($PSBoundParameters.ContainsKey('Domain')){$arguments+=@('-Domain',$Domain)}
if($MotorControl){$arguments+='-MotorControl'}
if($AimControl){$arguments+='-AimControl'}
if($Lidar){$arguments+='-Lidar'}
if($NavigationFollowProfile){$arguments+=@('-NavigationFollowProfile',$NavigationFollowProfile)}
& (Join-Path $PSScriptRoot 'run-env.ps1') -Command $arguments
exit $LASTEXITCODE
