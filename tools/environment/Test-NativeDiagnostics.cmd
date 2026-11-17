@echo off
setlocal
if not defined RMSIM_VCVARS set "RMSIM_VCVARS=D:\VS\Visual studio\VC\Auxiliary\Build\vcvars64.bat"
call "%RMSIM_VCVARS%" >nul
if errorlevel 1 exit /b 3
powershell -NoProfile -File "%~dp0Test-NativeDiagnostics.ps1"
exit /b %errorlevel%
