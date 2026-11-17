$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Push-Location (Join-Path $root 'ros2_ws')
try {
    & (Join-Path $PSScriptRoot 'run-env.ps1') -Command @('colcon','--log-base','log_control','build','--base-paths','src',
        '--packages-up-to','chassis_controllers','sp_msgs','pb_rm_interfaces','--build-base','build_control','--install-base','install_control',
        '--cmake-args','-G','Ninja','-DBUILD_TESTING=OFF','-DCMAKE_BUILD_TYPE=Release')
    if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
    & (Join-Path $PSScriptRoot 'run-env.ps1') -Command @('python',(Join-Path $PSScriptRoot 'stage_controller_runtime.py'))
    exit $LASTEXITCODE
} finally { Pop-Location }
