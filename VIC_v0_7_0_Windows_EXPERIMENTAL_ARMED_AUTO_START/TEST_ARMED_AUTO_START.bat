@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo VIC v0.7.0 ARMED AUTO START TEST
echo ================================================================
echo.
python tools\regression_test_armed_auto_start.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  echo You can run ROLLBACK_TO_V0_6_9.bat.
  pause
  exit /b 1
)
echo.
echo ALL ARMED AUTO START TESTS PASSED
pause
