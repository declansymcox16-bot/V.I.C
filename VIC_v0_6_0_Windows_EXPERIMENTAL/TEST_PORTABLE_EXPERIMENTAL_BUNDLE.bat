@echo off
setlocal
cd /d "%~dp0"
echo Running VIC v0.6.0 portable experimental regression test...
python tools\regression_test_portable_bundle.py
if errorlevel 1 (
 echo.
 echo TEST FAILED
 pause
 exit /b 1
)
echo.
echo TEST PASSED
pause
