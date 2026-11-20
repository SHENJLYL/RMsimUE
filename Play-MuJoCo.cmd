@echo off
setlocal
rem Launch the verified native MuJoCo backend with the existing RMUC UE scene.
if not defined UE_ROOT set "UE_ROOT=E:\simulator-tools\UE_5.8"
set "SPAWN_ID=default"
if not "%~1"=="" set "SPAWN_ID=%~1"
powershell.exe -NoProfile -File "%~dp0tools\unreal\Start-PreparedVehicle.ps1" -EngineRoot "%UE_ROOT%" -MapFile "%~dp0assets\maps\rmuc2027\map.json" -SpawnId "%SPAWN_ID%" -PhysicsBackend mujoco -MujocoModel "%~dp0assets\vehicles\aim_sentry\mujoco\rmuc2027\manifest.json" -SimStepSeconds 0.005 -SimRealTimePace -ViewportHz 60
if errorlevel 20 (
  timeout /t 5 /nobreak >nul
  exit /b 0
)
if errorlevel 1 pause
