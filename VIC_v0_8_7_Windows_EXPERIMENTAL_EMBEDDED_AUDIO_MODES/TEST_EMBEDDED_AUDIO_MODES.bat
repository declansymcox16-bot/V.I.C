@echo off
setlocal
cd /d "%~dp0"
title VIC Embedded Audio Modes Test
echo ================================================================
echo VIC v0.8.7 EMBEDDED AUDIO MODES TEST
echo ================================================================
echo.
python tools\regression_test_embedded_audio_modes.py
if errorlevel 1 (
 echo.
 echo TEST FAILED
 echo You can run ROLLBACK_TO_V0_8_6.bat.
 pause
 exit /b 1
)
echo.
echo ALL EMBEDDED AUDIO MODE TESTS PASSED
pause
