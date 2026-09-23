@echo off
setlocal
title VIC Worker
cd /d "%~dp0"
python -c "import psutil, yt_dlp" >nul 2>nul
if errorlevel 1 (
 echo VIC worker packages are missing. Running the installer now...
 call "%~dp0INSTALL_VIC.bat"
 if errorlevel 1 exit /b 1
)
python "%~dp0worker\worker.py"
pause
endlocal
