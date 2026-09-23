@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0tools\ffmpeg_compatible" mkdir "%~dp0tools\ffmpeg_compatible"
start "" "%~dp0tools\ffmpeg_compatible"
endlocal
