@echo off
setlocal
title VIC v0.3.2 System Check
cd /d "%~dp0"
echo ==========================================
echo VIC v0.3.2 System Check
echo ==========================================
echo.
echo [1] Python
where python
python --version
echo.
echo [2] Required Python packages
python -c "import flask, psutil, yt_dlp, soundcard, numpy; print('OK - Flask, psutil, yt-dlp, SoundCard and NumPy')" 2>nul
if errorlevel 1 echo ERROR - Run INSTALL_VIC.bat
echo.
echo [3] FFmpeg
python "%~dp0tools\check_ffmpeg.py"
echo.
echo [4] Project files
if exist "%~dp0dashboard\app.py" (echo OK - dashboard) else (echo ERROR - dashboard missing)
if exist "%~dp0worker\worker.py" (echo OK - worker) else (echo ERROR - worker missing)
echo.
pause
endlocal
