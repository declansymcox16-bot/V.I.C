@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo VIC v0.6.4 AUDIO MOTION AND 4K PREVIEW TEST
echo ================================================================
echo.
python tools\regression_test_audio_motion_and_4k.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  echo You can run ROLLBACK_TO_V0_6_3.bat.
  pause
  exit /b 1
)
echo.
echo ALL AUDIO MOTION AND 4K TESTS PASSED
pause
