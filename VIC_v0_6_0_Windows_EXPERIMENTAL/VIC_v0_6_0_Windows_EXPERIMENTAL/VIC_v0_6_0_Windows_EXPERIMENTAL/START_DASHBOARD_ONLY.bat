@echo off
setlocal
cd /d "%~dp0"
title Start VIC Dashboard
python -c "import flask, psutil" >nul 2>nul
if errorlevel 1 (
 call "%~dp0INSTALL_VIC.bat"
 if errorlevel 1 exit /b 1
)
python "%~dp0tools\process_manager.py" start-dashboard
python "%~dp0tools\process_manager.py" status
pause
endlocal
