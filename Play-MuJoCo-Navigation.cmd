@echo off
setlocal
if not defined UE_ROOT set "UE_ROOT=E:\simulator-tools\UE_5.8"
powershell.exe -NoProfile -File "%~dp0tools\environment\Start-NavigationConsole.ps1" -Scene rmuc2027 -EngineRoot "%UE_ROOT%" -PhysicsBackend mujoco -MujocoModel "%~dp0assets\vehicles\aim_sentry\mujoco\rmuc2027\manifest.json" -SimStep 0.005 -PaceRealTime -ReplaceManualPreview
if errorlevel 1 pause
