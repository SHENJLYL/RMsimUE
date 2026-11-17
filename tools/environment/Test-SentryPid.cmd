@echo off
setlocal
if not defined RMSIM_VCVARS set "RMSIM_VCVARS=D:\VS\Visual studio\VC\Auxiliary\Build\vcvars64.bat"
call "%RMSIM_VCVARS%" >nul
if errorlevel 1 exit /b 3
set "RMSIM_ROOT=%~dp0..\.."
if not exist "%RMSIM_ROOT%\artifacts\control\lk-motor-loop\pid" mkdir "%RMSIM_ROOT%\artifacts\control\lk-motor-loop\pid"
pushd "%RMSIM_ROOT%\artifacts\control\lk-motor-loop\pid"
cl /nologo /EHsc /std:c++17 /I"%RMSIM_ROOT%\ros2_ws\src\chassis_controllers\include" "%RMSIM_ROOT%\tests\control\test_sentry_pid.cpp" "%RMSIM_ROOT%\ros2_ws\src\chassis_controllers\src\utils\pid.cpp" /Fe:pid-sample-test.exe
if errorlevel 1 exit /b 3
pid-sample-test.exe
if errorlevel 1 exit /b 3
echo Native PID nominal and variable sample checks passed.
popd
