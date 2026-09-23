@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo VIC v0.6.9 BULK SOURCE CONTROL TEST
echo ================================================================
echo.
python tools\regression_test_bulk_source_control.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  echo You can run ROLLBACK_TO_V0_6_8.bat.
  pause
  exit /b 1
)
echo.
echo ALL BULK SOURCE CONTROL TESTS PASSED
pause
