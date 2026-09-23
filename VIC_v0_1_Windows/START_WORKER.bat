@echo off
setlocal
title VIC - Worker
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found.
    pause
    exit /b 1
)
python "%~dp0worker\worker.py"
pause
endlocal
