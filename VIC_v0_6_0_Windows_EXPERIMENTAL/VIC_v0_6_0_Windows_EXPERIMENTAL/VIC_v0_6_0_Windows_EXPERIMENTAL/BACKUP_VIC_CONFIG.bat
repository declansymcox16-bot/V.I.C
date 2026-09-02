@echo off
setlocal
cd /d "%~dp0"
python tools\config_backup.py create
pause
