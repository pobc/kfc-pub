@echo off
setlocal EnableExtensions EnableDelayedExpansion

if /i not "%~1"=="--worker" (
    set "WORKER=%TEMP%\kfc-update-worker-%RANDOM%-%RANDOM%.bat"
    copy /y "%~f0" "!WORKER!" >nul
    if errorlevel 1 (
        echo [ERROR] Failed to create temporary update worker.
        exit /b 1
    )

    call "!WORKER!" --worker "%~dp0" "%~1"
    set "EXIT_CODE=!ERRORLEVEL!"
    del /q "!WORKER!" >nul 2>&1
    exit /b !EXIT_CODE!
)

set "REPO_DIR=%~2"
set "RUN_MODE=%~3"
set "BRANCH=main"
cd /d "%REPO_DIR%"

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 goto not_repository

set "NO_UPDATE_CHECKS=0"

echo Watching origin/%BRANCH% for updates every 5 seconds.
echo No-update status is logged every 5 minutes.
echo Press Ctrl+C to stop.

:check
git fetch origin "+refs/heads/%BRANCH%:refs/remotes/origin/%BRANCH%" --quiet
if errorlevel 1 goto fetch_failed

set "LOCAL_COMMIT="
set "REMOTE_COMMIT="
for /f "delims=" %%L in ('git rev-parse HEAD 2^>nul') do set "LOCAL_COMMIT=%%L"
for /f "delims=" %%R in ('git rev-parse "origin/%BRANCH%" 2^>nul') do set "REMOTE_COMMIT=%%R"
if not defined LOCAL_COMMIT goto sync_updates
if not defined REMOTE_COMMIT goto fetch_failed
if /i not "!LOCAL_COMMIT!"=="!REMOTE_COMMIT!" goto sync_updates

set /a NO_UPDATE_CHECKS+=1 >nul
if !NO_UPDATE_CHECKS! LSS 60 goto wait
echo [%date% %time%] No updates.
set "NO_UPDATE_CHECKS=0"
goto wait

:sync_updates
echo [%date% %time%] Remote state changed. Overwriting local files with origin/!BRANCH!...
git checkout -f -B "!BRANCH!" "origin/!BRANCH!"
if errorlevel 1 goto pull_failed
git reset --hard "origin/!BRANCH!"
if errorlevel 1 goto pull_failed
echo [%date% %time%] Update completed.
set "NO_UPDATE_CHECKS=0"
goto wait

:fetch_failed
echo [%date% %time%] GitHub check failed. Retrying in 5 seconds.
goto wait

:pull_failed
echo [%date% %time%] Update failed. Retrying in 5 seconds.
goto wait

:wait
if /i "%RUN_MODE%"=="--once" exit /b 0
timeout /t 5 /nobreak >nul
goto check

:not_repository
echo [ERROR] %REPO_DIR% is not a Git repository.
if /i "%RUN_MODE%"=="--once" exit /b 1
pause
exit /b 1
