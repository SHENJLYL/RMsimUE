@echo off
setlocal
title RM Simulator - Unified Launcher
set "PY_PORTABLE=%~dp0runtime\ros2_env\python.exe"
set "PY_VENV=%~dp0artifacts\environments\mujoco-4.0.1\Scripts\python.exe"
if exist "%PY_PORTABLE%" (
  "%PY_PORTABLE%" "%~dp0tools\launcher\rmsim_launcher.py" %*
) else if exist "%PY_VENV%" (
  "%PY_VENV%" "%~dp0tools\launcher\rmsim_launcher.py" %*
) else (
  python "%~dp0tools\launcher\rmsim_launcher.py" %*
)
if errorlevel 1 pause
exit /b %ERRORLEVEL%
