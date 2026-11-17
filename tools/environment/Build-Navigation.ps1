$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$env:CMAKE_BUILD_PARALLEL_LEVEL='4'
Push-Location (Join-Path $root 'ros2_ws')
try {
 & (Join-Path $PSScriptRoot 'run-env.ps1') -Command @('colcon','--log-base','log_navigation','build','--base-paths','src',
  '--packages-select','loam_interface','sensor_scan_generation','fake_vel_transform','terrain_analysis','terrain_analysis_ext','pointcloud_to_laserscan','pb_omni_pid_pursuit_controller','pb_nav2_plugins','diagnostic_updater','nav2_lifecycle_manager',
  '--build-base','build_navigation','--install-base','install_navigation','--executor','sequential',
  '--cmake-args','-G','Ninja','-DBUILD_TESTING=OFF','-DCMAKE_BUILD_TYPE=Release','-Udiagnostic_updater_DIR')
 if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
 & (Join-Path $PSScriptRoot 'run-env.ps1') -Command @('python',(Join-Path $PSScriptRoot 'stage_navigation_runtime.py'))
 exit $LASTEXITCODE
}finally{Pop-Location}
