@echo off
setlocal
cd /d "%~dp0"
title Stop VIC Worker and Verify
python "%~dp0tools\process_manager.py" stop-worker
python "%~dp0tools\process_manager.py" status
pause
endlocal
