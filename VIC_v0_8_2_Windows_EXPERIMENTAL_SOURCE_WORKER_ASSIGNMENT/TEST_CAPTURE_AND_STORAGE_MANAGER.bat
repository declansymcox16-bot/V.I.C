@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo VIC v0.8.0 CAPTURE AND STORAGE MANAGER TEST
echo ================================================================
echo.
python tools\regression_test_capture_storage_manager.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  echo You can run ROLLBACK_TO_V0_7_1.bat.
  pause
  exit /b 1
)
echo.
echo ALL CAPTURE AND STORAGE MANAGER TESTS PASSED
pause
