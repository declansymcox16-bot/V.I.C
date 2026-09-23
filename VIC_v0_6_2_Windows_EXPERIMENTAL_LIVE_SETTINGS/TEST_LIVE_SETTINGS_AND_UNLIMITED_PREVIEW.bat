@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo VIC v0.6.2 LIVE SETTINGS AND UNLIMITED PREVIEW TEST
 echo ================================================================
echo.
python tools\regression_test_live_settings.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  echo Use ROLLBACK_TO_V0_6_1.bat to return to the working release.
  pause
  exit /b 1
)
echo.
echo ALL LIVE SETTINGS TESTS PASSED
pause
