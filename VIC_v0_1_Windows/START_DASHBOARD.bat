@echo off
setlocal
title VIC - Dashboard
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

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo WARNING: FFmpeg was not found on PATH.
    echo The dashboard will start, but RTSP recording will not work.
    echo.
)

python "%~dp0dashboard\app.py"

echo.
echo Dashboard stopped.
pause
endlocal
