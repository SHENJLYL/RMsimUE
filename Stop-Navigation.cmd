@echo off
setlocal
powershell -NoProfile -File "%~dp0tools\environment\Stop-NavigationConsole.ps1"
if errorlevel 1 pause
