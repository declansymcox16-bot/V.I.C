@echo off
setlocal
cd /d "%~dp0"
title Allow VIC Live Relay Firewall Ports
net session >nul 2>nul
if errorlevel 1 (
  echo Administrator permission is required to add the firewall rule.
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
echo Adding inbound TCP firewall rule for VIC live relay ports 39000-39199...
netsh advfirewall firewall delete rule name="VIC Distributed Live Relay TCP" >nul 2>nul
netsh advfirewall firewall add rule name="VIC Distributed Live Relay TCP" dir=in action=allow protocol=TCP localport=39000-39199 profile=private
if errorlevel 1 (
  echo.
  echo The firewall rule could not be added.
  pause
  exit /b 1
)
echo.
echo SUCCESS: This worker can accept live-processing relay connections on a Private network.
pause
