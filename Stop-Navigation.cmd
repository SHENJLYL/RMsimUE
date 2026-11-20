@echo off
setlocal
powershell -NoProfile -File "%~dp0tools\environment\Stop-NavigationConsole.ps1"
set "RMSIM_NAV_EXIT=%ERRORLEVEL%"
if not "%RMSIM_NAV_EXIT%"=="0" pause
exit /b %RMSIM_NAV_EXIT%
