@echo off
setlocal
title VIC - System Check
cd /d "%~dp0"
echo ==========================================
echo VIC v0.2 System Check
echo ==========================================
echo.
echo [1] Python
where python
python --version
echo.
echo [2] Project files
if exist "%~dp0dashboard\app.py" (echo OK dashboard\app.py) else (echo ERROR dashboard\app.py missing)
if exist "%~dp0worker\worker.py" (echo OK worker\worker.py) else (echo ERROR worker\worker.py missing)
echo.
echo [3] Flask
python -c "import flask; print('Flask OK')" 2>nul
if errorlevel 1 echo ERROR Flask missing - run INSTALL_VIC.bat
echo.
echo [4] FFmpeg
python "%~dp0tools\check_ffmpeg.py"
echo.
echo Check complete.
pause
endlocal
