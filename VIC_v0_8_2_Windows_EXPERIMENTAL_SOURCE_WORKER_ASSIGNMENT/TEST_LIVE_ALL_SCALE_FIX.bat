@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo VIC v0.6.8 LIVE ALL SCALE FIX TEST
echo ================================================================
echo.
python tools\regression_test_live_all_scale_fix.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  echo You can run ROLLBACK_TO_V0_6_7.bat.
  pause
  exit /b 1
)
echo.
echo ALL LIVE ALL SCALE FIX TESTS PASSED
pause
