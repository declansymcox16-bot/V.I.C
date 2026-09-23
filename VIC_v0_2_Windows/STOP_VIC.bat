@echo off
title VIC - Stop
echo WARNING: This stops every local python.exe process.
choice /M "Continue"
if errorlevel 2 exit /b
taskkill /F /IM python.exe >nul 2>nul
echo Done.
pause
