@echo off
setlocal
cd /d "%~dp0"
title VIC GPU Encoder Diagnostic
python tools\test_gpu_encoder.py
echo.
echo The results above explain exactly why VIC selected GPU or CPU.
pause
