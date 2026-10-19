@echo off
chcp 65001
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0" || exit /b 1

echo LoomStroke

REM Prefer the local interpreter even when Python is absent from system PATH.
set "LOOM_PYTHON=python"
if exist "venv\Scripts\python.exe" set "LOOM_PYTHON=%CD%\venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "LOOM_PYTHON=%CD%\.venv\Scripts\python.exe"
REM Preserve the venv environment for tools started by the application.
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
if not exist ".venv\Scripts\activate.bat" if exist "venv\Scripts\activate.bat" call "venv\Scripts\activate.bat"

REM Use python.exe (not pythonw) so Qt's Wintab/Windows Ink tablet
REM initialization succeeds. pythonw has no stdin/stdout which breaks
REM COM initialization in Qt 6 + Windows, causing QTabletEvent.pressure()
REM to return 0.0 permanently (tablet detected but pressure flat).
REM `start "" /min` launches the app detached in a minimized console so
REM this .bat terminates immediately and no foreground console remains.
start "" /min "%LOOM_PYTHON%" -m loomstroke
set "LOOM_EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %LOOM_EXIT_CODE%
