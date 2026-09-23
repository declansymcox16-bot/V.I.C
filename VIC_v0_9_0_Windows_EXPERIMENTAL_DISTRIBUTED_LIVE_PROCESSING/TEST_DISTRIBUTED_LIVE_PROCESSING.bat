@echo off
setlocal
cd /d "%~dp0"
title VIC Distributed Live Processing Test
echo ================================================================
echo VIC v0.9.0 DISTRIBUTED LIVE PROCESSING TEST
echo ================================================================
echo.
python tools\regression_test_distributed_live_processing.py
if errorlevel 1 (
 echo.
 echo TEST FAILED
 echo You can run ROLLBACK_TO_V0_8_7.bat.
 pause
 exit /b 1
)
echo.
echo ALL DISTRIBUTED LIVE PROCESSING TESTS PASSED
pause
