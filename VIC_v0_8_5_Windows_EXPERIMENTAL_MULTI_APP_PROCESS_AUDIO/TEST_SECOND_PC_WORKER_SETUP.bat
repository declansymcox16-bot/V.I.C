@echo off
setlocal
cd /d "%~dp0"
title Test VIC Second-PC Worker Setup
python "%~dp0tools\test_second_pc_worker_setup.py"
if errorlevel 1 (
 echo.
 echo TEST FAILED
 echo Run REPAIR_AND_SETUP_WORKER.bat.
 pause
 exit /b 1
)
echo.
pause
