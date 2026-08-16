@echo off
setlocal
cd /d "%~dp0"
echo This removes the plugin, its Marketplace registration, and its owned isolated runtime.
echo User Word and PPT project folders are not searched or deleted.
set /p "CONFIRM=Type YES to continue: "
if /I not "%CONFIRM%"=="YES" exit /b 1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" -RemoveRuntime -RemoveMarketplace
set "EXIT_CODE=%ERRORLEVEL%"
echo.
pause
exit /b %EXIT_CODE%
