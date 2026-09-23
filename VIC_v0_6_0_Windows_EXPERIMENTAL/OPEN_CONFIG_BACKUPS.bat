@echo off
cd /d "%~dp0"
if not exist config_backups mkdir config_backups
start "" explorer.exe "%~dp0config_backups"
