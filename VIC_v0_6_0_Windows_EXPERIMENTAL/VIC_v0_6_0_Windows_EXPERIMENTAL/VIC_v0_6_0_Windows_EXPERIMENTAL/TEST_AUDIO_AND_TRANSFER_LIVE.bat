@echo off
setlocal
cd /d "%~dp0"
echo Running VIC audio and transfer regression tests...
python tools\regression_test_audio_and_transfers.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  pause
  exit /b 1
)
echo.
echo TEST PASSED
pause
