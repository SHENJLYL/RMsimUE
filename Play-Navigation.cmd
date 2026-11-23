@echo off
setlocal
title RMSim - UE5 + RViz
if not defined UE_ROOT set "UE_ROOT=E:\simulator-tools\UE_5.8"
set "SCENE=rmuc2027"
if not "%~1"=="" set "SCENE=%~1"
set "RMUC_DISPLAY="
if /I "%SCENE%"=="rmuc2027" set "RMUC_DISPLAY=-RenderScale 50 -ViewportHz 120"
powershell -NoProfile -File "%~dp0tools\environment\Start-NavigationConsole.ps1" -Scene "%SCENE%" -EngineRoot "%UE_ROOT%" -PhysicsBackend mujoco -SimStep 0.005 -PaceRealTime -ReplaceManualPreview %RMUC_DISPLAY%
set "RMSIM_NAV_EXIT=%ERRORLEVEL%"
if not "%RMSIM_NAV_EXIT%"=="0" pause
exit /b %RMSIM_NAV_EXIT%
