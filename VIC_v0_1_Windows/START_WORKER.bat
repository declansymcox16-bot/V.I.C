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

if not exist "%~dp0worker\worker.py" (
    echo ERROR: worker\worker.py is missing.
    pause
    exit /b 1
)

python "%~dp0worker\worker.py"

echo.
echo Worker stopped.
pause
endlocal
