@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo VIC v0.6.5 SOURCE IMPORT FORMAT TEST
echo ================================================================
echo.
python tools\regression_test_source_import_formats.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  echo You can run ROLLBACK_TO_V0_6_4.bat.
  pause
  exit /b 1
)
echo.
echo ALL SOURCE IMPORT FORMAT TESTS PASSED
pause
