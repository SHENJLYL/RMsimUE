@echo off
setlocal
if not defined RMSIM_VCVARS set "RMSIM_VCVARS=D:\VS\Visual studio\VC\Auxiliary\Build\vcvars64.bat"
call "%RMSIM_VCVARS%" >nul
if errorlevel 1 exit /b 3
cd /d "%~dp0..\.."
if not exist "artifacts\navigation\mutex" mkdir "artifacts\navigation\mutex"
cl /nologo /EHsc /std:c++17 /MD /Fe:artifacts\navigation\mutex\default.exe /Fo:artifacts\navigation\mutex\default.obj tools\integration\native_mutex_probe.cpp
if errorlevel 1 exit /b 3
cl /nologo /EHsc /std:c++17 /MD /D_DISABLE_CONSTEXPR_MUTEX_CONSTRUCTOR /Fe:artifacts\navigation\mutex\compatible.exe /Fo:artifacts\navigation\mutex\compatible.obj tools\integration\native_mutex_probe.cpp
exit /b %errorlevel%
