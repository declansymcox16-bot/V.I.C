@echo off
setlocal
cd /d "%~dp0"
echo This will extract the original working VIC v0.5.1 beside this experimental folder.
set /p CONFIRM=Type ROLLBACK to continue: 
if /I not "%CONFIRM%"=="ROLLBACK" exit /b 1
set "DEST=%~dp0..\VIC_v0_5_1_ROLLBACK"
if exist "%DEST%" (
 echo Destination already exists: %DEST%
 pause
 exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%~dp0rollback\VIC_v0_5_1_Windows.zip' -DestinationPath '%DEST%' -Force"
echo.
echo Extracted to: %DEST%
echo Copy config\sources.json from this experimental folder only if you want its latest source changes.
pause
