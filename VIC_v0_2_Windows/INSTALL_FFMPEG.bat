@echo off
setlocal
title VIC - Install FFmpeg
cd /d "%~dp0"
where winget >nul 2>nul
if errorlevel 1 (
 echo ERROR: WinGet was not found.
 echo Open help\FFMPEG_HELP.html for alternatives.
 pause
 exit /b 1
)
winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements
echo.
echo Close old Command Prompt and VIC windows, then run CHECK_SYSTEM.bat.
pause
endlocal
