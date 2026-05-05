@echo off
setlocal

set PYTHON=C:\Users\User\miniconda3\envs\ml\python.exe
set BOT_DIR=%~dp0

echo =========================================
echo   Kana Bot - Starting...
echo   Python: %PYTHON%
echo   Dir:    %BOT_DIR%
echo =========================================

if not exist "%PYTHON%" (
    echo [ERROR] Python not found: %PYTHON%
    pause
    exit /b 1
)

if not exist "%BOT_DIR%.env" (
    echo [ERROR] .env not found: %BOT_DIR%.env
    pause
    exit /b 1
)

cd /d "%BOT_DIR%"

if not exist "%BOT_DIR%data\kana.db" (
    echo [INIT] DB not found, initializing...
    "%PYTHON%" status.py --reset
) else (
    for %%A in ("%BOT_DIR%data\kana.db") do (
        if %%~zA==0 (
            echo [INIT] DB is empty, initializing...
            "%PYTHON%" status.py --reset
        )
    )
)

echo [START] %date% %time%
"%PYTHON%" src\bot.py

pause
