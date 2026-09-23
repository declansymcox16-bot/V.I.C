@echo off
setlocal
title VIC - Video Ingest Cluster
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
 echo ERROR: Python was not found.
 pause
 exit /b 1
)
if not exist "%~dp0dashboard\app.py" (
 echo ERROR: dashboard\app.py is missing.
 pause
 exit /b 1
)
python "%~dp0dashboard\app.py"
echo.
echo VIC stopped.
pause
endlocal
