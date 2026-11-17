$ErrorActionPreference='Stop';$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$env:ROS_LOCALHOST_ONLY='1';$env:ROS_DOMAIN_ID='176'
$build=Join-Path $root 'artifacts/navigation/diagnostics-probe/build'
$extra=@();$fixed=Join-Path $root 'ros2_ws/install_navigation/diagnostic_updater/share/diagnostic_updater/cmake'
if(Test-Path -LiteralPath $fixed){$extra+='-Ddiagnostic_updater_DIR='+$fixed}
& (Join-Path $PSScriptRoot 'run-env.ps1') -Command (@('cmake','-S',(Join-Path $root 'tools/integration/native-diagnostics'),'-B',$build,'-G','Ninja','-DCMAKE_BUILD_TYPE=RelWithDebInfo')+$extra)
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
& (Join-Path $PSScriptRoot 'run-env.ps1') -Command @('cmake','--build',$build)
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
Copy-Item -LiteralPath (Join-Path $build 'diagnostics_probe.exe') -Destination (Join-Path $root 'artifacts/runtime/navigation/windows/diagnostics_probe.exe')
& (Join-Path $root 'artifacts/runtime/navigation/windows/diagnostics_probe.exe') *> (Join-Path $root 'artifacts/navigation/diagnostics-probe/runtime.log')
$code=$LASTEXITCODE
Get-Content -LiteralPath (Join-Path $root 'artifacts/navigation/diagnostics-probe/runtime.log') -Encoding UTF8
Write-Output ('Diagnostic probe exit '+$code)
