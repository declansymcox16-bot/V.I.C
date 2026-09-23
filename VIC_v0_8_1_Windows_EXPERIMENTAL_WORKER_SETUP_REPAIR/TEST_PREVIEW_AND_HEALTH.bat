@echo off
cd /d "%~dp0"
python tools\regression_test_health_preview.py
if errorlevel 1 (echo TEST FAILED&pause&exit /b 1)
echo ALL TESTS PASSED
pause
