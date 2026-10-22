@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem Keep this block together: the updater can replace this batch while it runs.
(
    "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1" -Root "%~dp0." %*
    if errorlevel 1 (
        echo.
        echo Update failed. Read the error above before retrying.
        if /i not "%~1"=="--no-pause" pause
        exit /b 1
    )
    if /i not "%~1"=="--no-pause" pause
    exit /b 0
)
