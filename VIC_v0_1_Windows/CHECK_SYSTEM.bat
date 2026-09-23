@echo off
setlocal
title VIC - System Check
cd /d "%~dp0"

echo ==========================================
echo VIC System Check v0.1
echo ==========================================
echo.

echo [1] Python
where python
python --version
echo.

echo [2] Dashboard file
if exist "%~dp0dashboard\app.py" (
    echo OK - dashboard\app.py found
) else (
    echo ERROR - dashboard\app.py missing
)
echo.

echo [3] Worker file
if exist "%~dp0worker\worker.py" (
    echo OK - worker\worker.py found
) else (
    echo ERROR - worker\worker.py missing
)
echo.

echo [4] Flask
python -c "import flask; print('Flask OK')" 2>nul
if errorlevel 1 (
    echo ERROR - Flask is not installed.
    echo Run INSTALL_DEPENDENCIES.bat
)
echo.

echo [5] FFmpeg
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo WARNING - FFmpeg was not found on PATH.
    echo RTSP recording will not work until FFmpeg is installed.
) else (
    ffmpeg -version | findstr /B /C:"ffmpeg version"
)
echo.

echo Check complete.
pause
endlocal
