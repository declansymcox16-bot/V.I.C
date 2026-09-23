@echo off
setlocal
cd /d "%~dp0"
title VIC Process Audio Helper Builder
echo ================================================================
echo VIC PROCESS AUDIO HELPER BUILDER
echo ================================================================
echo.
where go >nul 2>nul
if errorlevel 1 (
  echo ERROR: Go was not found on PATH.
  echo Install Go for Windows from https://go.dev/dl/ and run this again.
  pause
  exit /b 1
)
if not exist "vic_process_audio.go" (
  echo ERROR: vic_process_audio.go is missing from this folder.
  pause
  exit /b 1
)
echo Building Windows x64 process-audio helper...
set GOOS=windows
set GOARCH=amd64
set CGO_ENABLED=0
go build -trimpath -ldflags="-s -w" -o vic_process_audio.exe vic_process_audio.go
if errorlevel 1 (
  echo.
  echo BUILD FAILED.
  pause
  exit /b 1
)
echo.
echo SUCCESS:
echo %CD%\vic_process_audio.exe
pause
endlocal
