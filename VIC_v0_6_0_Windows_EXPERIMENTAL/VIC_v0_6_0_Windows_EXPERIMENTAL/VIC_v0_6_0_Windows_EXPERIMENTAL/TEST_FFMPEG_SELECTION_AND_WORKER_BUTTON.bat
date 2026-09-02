@echo off
setlocal
cd /d "%~dp0"
echo Testing VIC compatible FFmpeg selection and Worker Setup button...
python tools\regression_test_ffmpeg_selection.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  pause
  exit /b 1
)
echo.
echo TEST PASSED
pause
