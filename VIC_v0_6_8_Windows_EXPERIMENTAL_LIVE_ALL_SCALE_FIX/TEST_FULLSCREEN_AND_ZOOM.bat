@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo VIC v0.6.6 FULLSCREEN AND ZOOM TEST
echo ================================================================
echo.
python tools\regression_test_fullscreen_zoom.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  echo You can run ROLLBACK_TO_V0_6_5.bat.
  pause
  exit /b 1
)
echo.
echo ALL FULLSCREEN AND ZOOM TESTS PASSED
pause
