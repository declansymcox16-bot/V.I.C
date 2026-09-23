@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo VIC v0.7.1 LIVE DISK RATE TEST
echo ================================================================
echo.
python tools\regression_test_live_disk_rate.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  echo You can run ROLLBACK_TO_V0_7_0.bat.
  pause
  exit /b 1
)
echo.
echo ALL LIVE DISK RATE TESTS PASSED
pause
