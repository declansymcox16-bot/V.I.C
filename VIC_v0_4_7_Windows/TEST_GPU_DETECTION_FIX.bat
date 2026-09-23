@echo off
setlocal
cd /d "%~dp0"
echo Running parser test...
python tools\regression_test_gpu_detection.py
if errorlevel 1 (pause & exit /b 1)
echo.
echo Running real GPU diagnostic...
python tools\test_gpu_encoder.py
pause
