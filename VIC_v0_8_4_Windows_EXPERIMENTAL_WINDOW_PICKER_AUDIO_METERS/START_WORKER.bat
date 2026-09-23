@echo off
setlocal
cd /d "%~dp0"
python "%~dp0tools\config_backup.py" create --quiet >nul 2>nul
title Start VIC Worker v0.8.1
python -c "import psutil, yt_dlp, soundcard, numpy; from PIL import Image" >nul 2>nul
if errorlevel 1 (
 echo One or more VIC worker packages are missing.
 echo Running the complete dependency repair now...
 call "%~dp0INSTALL_VIC.bat"
 if errorlevel 1 exit /b 1
)
echo.
echo Running the second-PC connection check...
python "%~dp0tools\test_second_pc_worker_setup.py" --quick
if errorlevel 1 (
 echo.
 echo Worker setup is not ready yet.
 echo Run REPAIR_AND_SETUP_WORKER.bat and follow the setup window.
 pause
 exit /b 1
)
echo.
echo Starting the worker. It will automatically search for the main VIC Dashboard if the saved address is unavailable.
python "%~dp0tools\process_manager.py" start-worker
echo.
python "%~dp0tools\process_manager.py" status
pause
endlocal
