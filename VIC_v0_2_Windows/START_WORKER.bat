@echo off
setlocal
title VIC - Worker
cd /d "%~dp0"
python "%~dp0worker\worker.py"
pause
endlocal
