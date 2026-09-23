@echo off
setlocal
title VIC - Installer
cd /d "%~dp0"
echo ==========================================
echo VIC v0.2 Installer
echo ==========================================
where python >nul 2>nul
if errorlevel 1 (
 echo ERROR: Python was not found.
 echo Install Python and enable Add Python to PATH.
 pause
 exit /b 1
)
python -m pip install --upgrade pip
python -m pip install -r "%~dp0requirements.txt"
echo.
echo Installation step finished. Next run CHECK_SYSTEM.bat
pause
endlocal
