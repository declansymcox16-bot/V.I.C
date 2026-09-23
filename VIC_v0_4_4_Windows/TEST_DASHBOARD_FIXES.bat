@echo off
setlocal
cd /d "%~dp0"
echo Running VIC Dashboard regression tests...
python tools\regression_test_dashboard.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  pause
  exit /b 1
)
echo.
echo ALL TESTS PASSED
pause
