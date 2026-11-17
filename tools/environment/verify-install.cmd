@echo off
call D:\simulator\tools\environment\smoke_ws\install\local_setup.bat
if errorlevel 1 exit /b %errorlevel%
D:\simulator\tools\environment\smoke_ws\install\lib\native_humble_probe\native_probe.exe
exit /b %errorlevel%
