@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "PID_FILE=%TEMP%\github-copilot-sessions-viewer.pid"

if exist "%PID_FILE%" (
  set /p TARGET_PID=<"%PID_FILE%"
  if defined TARGET_PID taskkill /PID !TARGET_PID! /T /F >nul 2>&1
  del /q "%PID_FILE%" >nul 2>&1
  goto end
)

for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8766" ^| findstr "LISTENING"') do (
  taskkill /PID %%P /F >nul 2>&1
)

:end
endlocal
