$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Push-Location (Join-Path $root 'ros2_ws')
try {
    & (Join-Path $PSScriptRoot 'run-env.ps1') -Command @('colcon','--log-base','log_rviz_coverage','build','--base-paths','src',
        '--packages-select','rmsim_rviz_coverage','--merge-install','--build-base','build_rviz_coverage','--install-base','install_rviz_coverage',
        '--cmake-args','-G','Ninja','-DCMAKE_BUILD_TYPE=Release')
    if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
}finally{Pop-Location}
& (Join-Path $PSScriptRoot 'run-env.ps1') -Command @('python',(Join-Path $PSScriptRoot 'stage_rviz_runtime.py'))
exit $LASTEXITCODE
