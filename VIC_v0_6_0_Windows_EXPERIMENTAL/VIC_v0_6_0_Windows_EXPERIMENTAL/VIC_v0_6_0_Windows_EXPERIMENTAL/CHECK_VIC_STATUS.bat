@echo off
setlocal
cd /d "%~dp0"
title VIC Status Check
python "%~dp0tools\process_manager.py" status
pause
endlocal
