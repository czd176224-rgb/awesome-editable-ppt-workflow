@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
  echo Installation completed. Restart Codex and create a new task.
) else (
  echo Installation failed. See the message above and docs\TROUBLESHOOTING.zh-CN.md.
)
pause
exit /b %EXIT_CODE%
