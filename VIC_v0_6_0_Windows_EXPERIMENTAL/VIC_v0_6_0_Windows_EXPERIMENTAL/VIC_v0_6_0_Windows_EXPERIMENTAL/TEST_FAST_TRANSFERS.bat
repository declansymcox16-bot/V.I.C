@echo off
setlocal
cd /d "%~dp0"
echo Running VIC fast-transfer regression test...
python tools\regression_test_fast_transfers.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  pause
  exit /b 1
)
echo.
echo TEST PASSED
pause
