$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Push-Location (Join-Path $root 'ros2_ws')
try {
    & (Join-Path $PSScriptRoot 'run-env.ps1') -Command @('colcon','--log-base','log_lio','build','--base-paths','src',
        '--packages-up-to','point_lio','--build-base','build_lio','--install-base','install_lio',
        '--executor','sequential','--cmake-args','-G','Ninja','-DBUILD_TESTING=OFF','-DCMAKE_BUILD_TYPE=Release')
    if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
    & (Join-Path $PSScriptRoot 'run-env.ps1') -Command @('python',(Join-Path $PSScriptRoot 'stage_controller_runtime.py'),
        '--install-root',(Join-Path $root 'ros2_ws/install_lio'),'--package','point_lio','--dependency-package','livox_ros_driver2')
    exit $LASTEXITCODE
} finally { Pop-Location }
