@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo VIC v0.8.2 SOURCE WORKER ASSIGNMENT TEST
echo ================================================================
echo.
python tools\regression_test_source_worker_assignment.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  echo You can run ROLLBACK_TO_V0_8_1.bat.
  pause
  exit /b 1
)
echo.
echo ALL SOURCE WORKER ASSIGNMENT TESTS PASSED
pause
