@echo off
setlocal
cd /d "%~dp0"
python "%~dp0tools\config_backup.py" create --quiet >nul 2>nul
title Start VIC
python -c "import flask, psutil, yt_dlp, soundcard, numpy" >nul 2>nul
if errorlevel 1 (
 echo VIC packages are missing. Running the installer now...
 call "%~dp0INSTALL_VIC.bat"
 if errorlevel 1 exit /b 1
)
echo Starting and checking the VIC Dashboard and Local Worker...
python "%~dp0tools\process_manager.py" start-all
echo.
if errorlevel 1 (
 echo VIC did not start correctly. Read the message above.
) else (
 echo VIC is running. You may close this small launcher window.
)
pause
endlocal
