@echo off
setlocal
title VIC Dashboard
cd /d "%~dp0"
python "%~dp0dashboard\app.py"
pause
endlocal
