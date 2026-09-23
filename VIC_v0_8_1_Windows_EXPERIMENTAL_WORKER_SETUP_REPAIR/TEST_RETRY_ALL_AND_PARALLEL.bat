@echo off
setlocal
cd /d "%~dp0"
echo Running VIC Retry All and parallel-slot regression test...
python tools\regression_test_retry_all_parallel.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  pause
  exit /b 1
)
echo.
echo TEST PASSED
pause
