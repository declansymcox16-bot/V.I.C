@echo off
setlocal
cd /d "%~dp0"
title Stop VIC and Verify
where python >nul 2>nul
if errorlevel 1 (
 echo ERROR: Python was not found, so VIC process verification cannot run.
 pause
 exit /b 1
)
echo Stopping VIC jobs, workers, FFmpeg children and Dashboard processes from this folder...
python "%~dp0tools\process_manager.py" stop-all
echo.
if errorlevel 1 (
 echo STOP CHECK FAILED. Read the process details above.
) else (
 echo STOP CHECK PASSED. VIC is fully stopped.
)
pause
endlocal
