@echo off
title Stop VIC
taskkill /F /T /FI "WINDOWTITLE eq VIC Dashboard*" >nul 2>nul
taskkill /F /T /FI "WINDOWTITLE eq VIC Local Worker*" >nul 2>nul
taskkill /F /T /FI "WINDOWTITLE eq VIC Worker*" >nul 2>nul
echo VIC windows were asked to stop.
pause
