@echo off
setlocal
if not defined UE_ROOT set "UE_ROOT=E:\simulator-tools\UE_5.8"
set "SCENE=rmuc2027"
if not "%~1"=="" set "SCENE=%~1"
powershell -NoProfile -File "%~dp0tools\environment\Start-NavigationConsole.ps1" -Scene "%SCENE%" -EngineRoot "%UE_ROOT%" -ReplaceManualPreview
if errorlevel 1 pause
