@echo off
setlocal
cd /d "%~dp0"
title VIC Worker Setup v0.8.2
python -c "import psutil, yt_dlp, soundcard, numpy; from PIL import Image" >nul 2>nul
if errorlevel 1 (
 echo Repairing all worker dependencies first...
 call "%~dp0INSTALL_VIC.bat"
 if errorlevel 1 exit /b 1
)
python "%~dp0tools\worker_setup_gui.py"
endlocal
