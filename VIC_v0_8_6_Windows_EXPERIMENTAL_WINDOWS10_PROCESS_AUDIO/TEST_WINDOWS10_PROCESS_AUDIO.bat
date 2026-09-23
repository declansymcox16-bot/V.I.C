@echo off
setlocal
cd /d "%~dp0"
title VIC Windows 10 Process Audio Test
echo ================================================================
echo VIC v0.8.6 WINDOWS 10 PROCESS-AUDIO COMPATIBILITY TEST
echo ================================================================
echo.
python tools\regression_test_windows10_process_audio_compatibility.py
if errorlevel 1 (
 echo.
 echo TEST FAILED
 echo You can run ROLLBACK_TO_V0_8_5.bat.
 pause
 exit /b 1
)
echo.
echo Static compatibility checks passed.
echo.
echo This computer's worker will report its real Windows build in the
echo application-window picker. Use Test on an application source for the
echo final real process-audio API test.
echo.
pause
