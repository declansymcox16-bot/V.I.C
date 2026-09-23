@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo VIC v0.8.4 WINDOW PICKER AND AUDIO METER TEST
echo ================================================================
echo.
python tools\regression_test_window_picker_audio_meters.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  echo You can run ROLLBACK_TO_V0_8_3.bat.
  pause
  exit /b 1
)
echo.
echo ALL WINDOW PICKER AND AUDIO METER TESTS PASSED
pause
