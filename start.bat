@echo off
rem Flow Kit: double-click to start the server and open http://127.0.0.1:8100
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
if errorlevel 1 exit /b 1
