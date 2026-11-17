param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Command)
$ErrorActionPreference = 'Stop'
$env:PIXI_CACHE_DIR = 'E:\simulator-cache\pixi'
$env:PIXI_HOME = 'E:\simulator-cache\pixi-home'
$env:PIXI_NO_CONFIG = '1'
$env:TEMP = 'E:\simulator-cache\tmp'
$env:TMP = $env:TEMP
$env:PYTHONNOUSERSITE = '1'
$env:ROS_LOG_DIR = 'E:\simulator-cache\ros-logs'
$env:COLCON_HOME = 'E:\simulator-cache\colcon-home'
& 'E:\simulator-tools\pixi\pixi.exe' run --manifest-path "$PSScriptRoot\pixi.toml" @Command
exit $LASTEXITCODE
