@echo off
setlocal
rem Fast launch of this already built/imported sample; no build, install or policy changes.
if not defined UE_ROOT set "UE_ROOT=E:\simulator-tools\UE_5.8"
if not exist "%UE_ROOT%\Engine\Binaries\Win64\UnrealEditor.exe" (
  echo UnrealEditor missing. Set UE_ROOT to the installed engine folder.
  pause
  exit /b 3
)
if not exist "%~dp0unreal\RMSim\Binaries\Win64\UnrealEditor-RMSim.dll" (
  echo Build the project using tools\unreal\Build-ImportLab.ps1 first.
  pause
  exit /b 3
)
if not exist "%~dp0assets\vehicles\aim_sentry\ue_mesh_rig.json" (
  echo Import the vehicle using tools\unreal\Import-VehicleAssets.ps1 first.
  pause
  exit /b 3
)
set "MAP_FILE=%~dp0assets\maps\rmuc2027\map.json"
if not "%~1"=="" set "MAP_FILE=%~f1"
set "SPAWN_ID=default"
if not "%~2"=="" set "SPAWN_ID=%~2"
set "TRAINING_FLAG="
if /I "%~3"=="training" set "TRAINING_FLAG=-TrainingMode"
if not exist "%MAP_FILE%" (
  echo Map not found: "%MAP_FILE%"
  pause
  exit /b 3
)
powershell.exe -NoProfile -File "%~dp0tools\unreal\Start-PreparedVehicle.ps1" -EngineRoot "%UE_ROOT%" -MapFile "%MAP_FILE%" -SpawnId "%SPAWN_ID%" -PhysicsBackend mujoco -MujocoModel "%~dp0assets\vehicles\aim_sentry\mujoco\rmuc2027\manifest.json" -SimStepSeconds 0.005 -SimRealTimePace -ViewportHz 120 -RenderScale 50 %TRAINING_FLAG%
if errorlevel 20 (
  timeout /t 5 /nobreak >nul
  exit /b 0
)
if errorlevel 1 pause
