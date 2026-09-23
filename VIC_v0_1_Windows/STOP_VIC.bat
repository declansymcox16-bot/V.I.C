@echo off
title VIC - Stop
echo Stopping VIC Python processes...
taskkill /F /IM python.exe >nul 2>nul
echo Done.
pause
