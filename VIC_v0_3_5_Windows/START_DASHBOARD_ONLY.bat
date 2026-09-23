@echo off
setlocal
title VIC Dashboard
cd /d "%~dp0"
python -c "import flask" >nul 2>nul
if errorlevel 1 (
 echo VIC dashboard packages are missing. Running the installer now...
 call "%~dp0INSTALL_VIC.bat"
 if errorlevel 1 exit /b 1
)
python "%~dp0dashboard\app.py"
pause
endlocal
