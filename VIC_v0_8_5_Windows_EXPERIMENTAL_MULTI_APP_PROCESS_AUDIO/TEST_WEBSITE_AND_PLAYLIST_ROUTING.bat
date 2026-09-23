@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo VIC v0.8.3 WEBSITE AND PLAYLIST ROUTING TEST
echo ================================================================
echo.
python tools\regression_test_website_playlist_routing.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  echo You can run ROLLBACK_TO_V0_8_2.bat.
  pause
  exit /b 1
)
echo.
echo ALL WEBSITE AND PLAYLIST ROUTING TESTS PASSED
pause
