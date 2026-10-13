@echo off
chcp 936 >nul
setlocal EnableExtensions

if not defined PG_BIN set "PG_BIN=C:\Program Files\PostgreSQL\17\bin"
if not defined PG_DATA set "PG_DATA=%~dp0.kfc\pgdata"
set "LANG=zh_CN"

if not exist "%PG_BIN%\pg_ctl.exe" (
    echo [错误] 未找到 pg_ctl.exe：
    echo         %PG_BIN%\pg_ctl.exe
    pause
    exit /b 1
)

if not exist "%PG_BIN%\postgres.exe" (
    echo [错误] 未找到 postgres.exe：
    echo         %PG_BIN%\postgres.exe
    pause
    exit /b 1
)

if not exist "%PG_DATA%\PG_VERSION" (
    echo [错误] PostgreSQL 数据目录无效：
    echo         %PG_DATA%
    pause
    exit /b 1
)

"%PG_BIN%\pg_ctl.exe" status -D "%PG_DATA%" >nul 2>&1
if not errorlevel 1 (
    echo [错误] PostgreSQL 已在其他进程中运行。
    echo [提示] 请先停止现有实例，再重新启动。
    pause
    exit /b 1
)

title KFC PostgreSQL（关闭窗口即停止）
echo [提示] PostgreSQL 已启动，关闭窗口即可停止。
echo.

"%PG_BIN%\postgres.exe" -D "%PG_DATA%" -c "lc_messages=Chinese (Simplified)_China.936"
set "PG_EXIT=%ERRORLEVEL%"

if "%PG_EXIT%"=="0" exit /b 0

echo.
echo [错误] PostgreSQL 异常退出，退出码：%PG_EXIT%。
pause
exit /b %PG_EXIT%
