@echo off
setlocal
cd /d "%~dp0"
title Start VIC Worker
python -c "import psutil, yt_dlp" >nul 2>nul
if errorlevel 1 (
 echo VIC worker packages are missing. Running the installer now...
 call "%~dp0INSTALL_VIC.bat"
 if errorlevel 1 exit /b 1
)
echo Starting the worker. It will automatically search for the main VIC Dashboard if the saved address is unavailable.
python "%~dp0tools\process_manager.py" start-worker
echo.
python "%~dp0tools\process_manager.py" status
pause
endlocal
