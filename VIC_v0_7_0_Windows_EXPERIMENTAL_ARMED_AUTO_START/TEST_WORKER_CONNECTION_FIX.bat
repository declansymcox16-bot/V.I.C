@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo VIC v0.6.1 WORKER CONNECTION FIX TEST
echo ================================================================
echo.
python tools\regression_test_worker_connection.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  echo You can use ROLLBACK_TO_V0_5_1.bat immediately.
  pause
  exit /b 1
)
echo.
echo ALL CONNECTION TESTS PASSED
pause
