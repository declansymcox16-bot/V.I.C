@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo VIC v0.6.7 LIVE ALL REFLOW TEST
echo ================================================================
echo.
python tools\regression_test_live_all_reflow.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  echo You can run ROLLBACK_TO_V0_6_6.bat.
  pause
  exit /b 1
)
echo.
echo ALL LIVE ALL REFLOW TESTS PASSED
pause
