param([int]$Port=9451,[switch]$MotorControl,[switch]$AimControl,[switch]$Lidar)
if($Port -lt 1024 -or $Port -gt 65535){throw 'Port must be 1024..65535.'}
$shell=(Get-Process -Id $PID).Path
$arguments=@($shell,'-NoProfile','-File',(Join-Path $PSScriptRoot '../integration/Run-InstalledBridge.ps1'),'-Port',$Port)
if($MotorControl){$arguments+='-MotorControl'}
if($AimControl){$arguments+='-AimControl'}
if($Lidar){$arguments+='-Lidar'}
& (Join-Path $PSScriptRoot 'run-env.ps1') -Command $arguments
exit $LASTEXITCODE
