@echo off
setlocal
set "TRAINING_MAP=%~dp0assets\maps\rmuc2027\map.json"
if not "%~1"=="" set "TRAINING_MAP=%~f1"
call "%~dp0Play-ManualVehicle.cmd" "%TRAINING_MAP%" default training
