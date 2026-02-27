@echo off
setlocal EnableExtensions EnableDelayedExpansion

for %%I in ("%~dp0..\..") do set "APP_DIR_WIN=%%~fI"
set "URL_TO_OPEN=http://127.0.0.1:8766"
set "MAX_WAIT=30"
set "PID_FILE=%TEMP%\github-copilot-sessions-viewer.pid"
set "PY_CMD="

echo Starting GitHub Copilot Sessions Viewer...
echo Viewer: %URL_TO_OPEN%
echo Sessions dir: (shown in the GitHubCopilotSessionViewer-window)
echo.

where py >nul 2>&1
if %errorlevel% == 0 set "PY_CMD=py -3"
if not defined PY_CMD (
  where python >nul 2>&1
  if %errorlevel% == 0 set "PY_CMD=python"
)
if not defined PY_CMD goto python_not_found

if exist "%PID_FILE%" (
  set /p OLD_PID=<"%PID_FILE%"
  if defined OLD_PID taskkill /PID !OLD_PID! /T /F >nul 2>&1
  del /q "%PID_FILE%" >nul 2>&1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$app='%APP_DIR_WIN%'; $pidFile='%PID_FILE%'; $py='%PY_CMD%';" ^
  "$cmd='title GitHubCopilotSessionViewe && cd /d \"' + $app + '\" && ' + $py + ' viewer.py';" ^
  "$p=Start-Process -FilePath 'cmd.exe' -ArgumentList '/k', $cmd -PassThru;" ^
  "Set-Content -Path $pidFile -Value $p.Id -Encoding ascii"
if errorlevel 1 goto startup_failed
timeout /t 1 /nobreak >nul

set /a WAITED=0
:wait_loop
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = New-Object Net.Sockets.TcpClient; try { $c.Connect('127.0.0.1', 8766); exit 0 } catch { exit 1 } finally { $c.Dispose() }" >nul 2>&1
if %errorlevel% == 0 goto open_browser

set /a WAITED+=1
if !WAITED! geq !MAX_WAIT! goto startup_failed
timeout /t 1 /nobreak >nul
goto wait_loop

:open_browser
echo Viewer started successfully.
echo Viewer: %URL_TO_OPEN%
echo Sessions dir: (shown in the GitHubCopilotSessionViewer-window)
echo.
start "" "%URL_TO_OPEN%"
goto end

:python_not_found
echo Python is not found.
echo Install Python, then ensure `py` or `python` is available in PATH.
goto fail_pause

:startup_failed
echo Viewer startup failed.
echo Diagnostic:
if exist "%PID_FILE%" (
  set /p CUR_PID=<"%PID_FILE%"
  echo pid: !CUR_PID!
  tasklist /FI "PID eq !CUR_PID!"
)
echo listening_8766:
netstat -ano | findstr ":8766" | findstr "LISTENING"
goto fail_pause

:fail_pause
echo.
echo Press any key to close this window...
pause >nul
exit /b 1

:end
endlocal
