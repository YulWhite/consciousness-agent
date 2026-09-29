@echo off
rem ================================================================
rem  Start consciousness-agent v0.8
rem  (ASCII + CRLF only - project encoding rule)
rem ================================================================
cd /d "%~dp0"

set PYTHONIOENCODING=utf-8

rem 1) prefer system python, else project-local python314
where python >nul 2>nul
if %errorlevel% equ 0 (
    set "PY=python"
) else if exist "D:\Python314\python.exe" (
    set "PY=D:\Python314\python.exe"
) else (
    echo [ERROR] Python not found. Install Python 3.9+ or edit this file.
    pause
    exit /b 1
)

"%PY%" main.py

rem keep window open after she sleeps / on crash
echo.
echo  --- session ended ---
pause