@echo off
setlocal
cd /d "%~dp0"
start "VIC Dashboard" cmd /k call "%~dp0START_DASHBOARD_ONLY.bat"
timeout /t 2 /nobreak >nul
start "VIC Local Worker" cmd /k call "%~dp0START_WORKER.bat"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8765"
endlocal
