@echo off
setlocal
title VIC v0.4.0 Installer
cd /d "%~dp0"
echo ==========================================
echo VIC v0.4.0 Dependency Installer
echo ==========================================
echo.
where python >nul 2>nul
if errorlevel 1 (
 echo ERROR: Python was not found.
 echo Install Python and enable Add Python to PATH.
 pause
 exit /b 1
)
echo Installing and repairing all VIC packages...
python -m pip install --upgrade pip
if errorlevel 1 goto :failed
python -m pip install --upgrade --disable-pip-version-check -r "%~dp0requirements.txt"
if errorlevel 1 goto :failed
python -c "import flask, psutil, yt_dlp, soundcard, numpy; print('VIC packages installed correctly')"
if errorlevel 1 goto :failed
echo.
echo SUCCESS: Flask, psutil, yt-dlp, SoundCard and NumPy are installed.
echo You can now run START_VIC.bat or START_WORKER.bat.
pause
exit /b 0
:failed
echo.
echo ERROR: VIC dependencies did not install correctly.
echo Copy the complete error above if you need help.
pause
exit /b 1
