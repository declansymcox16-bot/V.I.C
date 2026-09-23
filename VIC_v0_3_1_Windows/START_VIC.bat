@echo off
setlocal
cd /d "%~dp0"
python -c "import flask, psutil, yt_dlp" >nul 2>nul
if errorlevel 1 (
 echo VIC packages are missing. Running the installer now...
 call "%~dp0INSTALL_VIC.bat"
 if errorlevel 1 exit /b 1
)
start "VIC Dashboard" cmd /k ""%~dp0START_DASHBOARD_ONLY.bat""
timeout /t 2 /nobreak >nul
start "VIC Local Worker" cmd /k ""%~dp0START_WORKER.bat""
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:8765"
endlocal
