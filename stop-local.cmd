@echo off
setlocal EnableExtensions

set "FOUND=0"
for /f "tokens=5" %%P in ('netstat -ano -p tcp ^| findstr /R /C:":3000 .*LISTENING"') do (
    set "FOUND=1"
    echo Stopping KFC process PID %%P...
    taskkill /PID %%P /T /F >nul 2>&1
)

if "%FOUND%"=="0" echo KFC process is not running on port 3000.
exit /b 0
