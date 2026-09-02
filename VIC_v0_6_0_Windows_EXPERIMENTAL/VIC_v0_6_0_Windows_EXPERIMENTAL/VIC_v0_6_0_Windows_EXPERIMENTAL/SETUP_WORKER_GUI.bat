@echo off
setlocal
cd /d "%~dp0"
title VIC Worker Setup
python -c "import psutil" >nul 2>nul
if errorlevel 1 (
 call "%~dp0INSTALL_VIC.bat"
 if errorlevel 1 exit /b 1
)
python "%~dp0tools\worker_setup_gui.py"
endlocal
