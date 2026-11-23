@echo off
setlocal
rem Open the imported Flanders scene with the validated D3D11 rendering path.
if not defined UE_ROOT set "UE_ROOT=E:\simulator-tools\UE_5.8"
set "SPAWN_ID=default"
if not "%~1"=="" set "SPAWN_ID=%~1"
powershell.exe -NoProfile -File "%~dp0tools\unreal\Start-PreparedVehicle.ps1" -EngineRoot "%UE_ROOT%" -MapFile "%~dp0assets\maps\wt_vlaanderen\map.json" -SpawnId "%SPAWN_ID%" -GraphicsRhi D3D11 -SingleShaderWorker
set "WT_LAUNCH_EXIT=%errorlevel%"
if "%WT_LAUNCH_EXIT%"=="20" (
  timeout /t 5 /nobreak >nul
  exit /b 0
)
if not "%WT_LAUNCH_EXIT%"=="0" pause
exit /b %WT_LAUNCH_EXIT%
