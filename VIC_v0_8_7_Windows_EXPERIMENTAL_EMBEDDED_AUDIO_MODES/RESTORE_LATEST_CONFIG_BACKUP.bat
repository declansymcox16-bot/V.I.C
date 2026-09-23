@echo off
setlocal
cd /d "%~dp0"
echo Stop VIC before restoring configuration.
set /p CONFIRM=Type RESTORE to continue: 
if /I not "%CONFIRM%"=="RESTORE" exit /b 1
python tools\config_backup.py restore-latest
pause
