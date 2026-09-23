@echo off
setlocal
cd /d "%~dp0"
title Repair and Setup VIC Worker v0.8.1
echo ================================================================
echo VIC v0.8.1 SECOND-PC WORKER REPAIR
echo ================================================================
echo.
echo Step 1 of 2: Repairing dependencies...
call "%~dp0INSTALL_VIC.bat"
if errorlevel 1 (
 echo.
 echo Dependency repair failed.
 pause
 exit /b 1
)
echo.
echo Step 2 of 2: Opening Worker Setup...
python "%~dp0tools\worker_setup_gui.py"
endlocal
