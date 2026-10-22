@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0boot_LoomStroke.bat" --no-ai %*
exit /b %ERRORLEVEL%
