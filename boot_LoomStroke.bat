@echo off
chcp 65001
setlocal enabledelayedexpansion

echo LoomStroke

REM Activate venv
if exist .venv\Scripts\activate call .venv\Scripts\activate
if not exist .venv\Scripts\activate if exist venv\Scripts\activate call venv\Scripts\activate

REM Use python.exe (not pythonw) so Qt's Wintab/Windows Ink tablet
REM initialization succeeds. pythonw has no stdin/stdout which breaks
REM COM initialization in Qt 6 + Windows, causing QTabletEvent.pressure()
REM to return 0.0 permanently (tablet detected but pressure flat).
REM `start "" /min` launches the app detached in a minimized console so
REM this .bat terminates immediately and no foreground console remains.
start "" /min python -m loomstroke
