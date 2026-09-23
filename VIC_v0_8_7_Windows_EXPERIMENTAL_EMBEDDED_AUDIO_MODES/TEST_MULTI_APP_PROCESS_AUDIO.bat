@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo VIC v0.8.5 MULTI APPLICATION + PROCESS AUDIO TEST
echo ================================================================
echo.
python tools\regression_test_multi_app_process_audio.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  echo You can run ROLLBACK_TO_V0_8_4.bat.
  pause
  exit /b 1
)
echo.
echo ALL MULTI APPLICATION AND PROCESS AUDIO TESTS PASSED
pause
