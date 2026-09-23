@echo off
setlocal
cd /d "%~dp0"
echo ================================================================
echo VIC v0.6.3 SOURCE FORM AND EXPORT TEST
echo ================================================================
echo.
python tools\regression_test_source_forms_and_export.py
if errorlevel 1 (
  echo.
  echo TEST FAILED
  echo You can run ROLLBACK_TO_V0_6_2.bat.
  pause
  exit /b 1
)
echo.
echo ALL SOURCE FORM AND EXPORT TESTS PASSED
pause
