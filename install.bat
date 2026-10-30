@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "INSTALL_ROOT=%~dp0"
set "PAUSE_ON_EXIT=1"
set "WITH_NANOBANANA=1"
set "BASE_PYTHON="
set "EXIT_CODE=1"
set "ALLOW_PYTHON_DOWNLOAD=1"

:parse_args
if "%~1"=="" goto :prepare
if /i "%~1"=="--no-pause" (
    set "PAUSE_ON_EXIT=0"
    shift
    goto :parse_args
)
if /i "%~1"=="--nanobanana" (
    set "WITH_NANOBANANA=1"
    shift
    goto :parse_args
)
if /i "%~1"=="--no-python-download" (
    set "ALLOW_PYTHON_DOWNLOAD=0"
    shift
    goto :parse_args
)
if /i "%~1"=="--python" (
    if "%~2"=="" goto :usage_error
    set "BASE_PYTHON=%~2"
    shift
    shift
    goto :parse_args
)
if /i "%~1"=="--help" goto :help
echo ERROR: Unknown option: %~1
goto :usage_error

:prepare
pushd "%INSTALL_ROOT%" || goto :finish
if not exist "pyproject.toml" (
    echo ERROR: Keep install.bat in the LoomStroke source folder.
    goto :done
)
set "VENV_DIR=%CD%\.venv"
if exist ".venv" goto :existing_venv
if exist "venv\Scripts\python.exe" set "VENV_DIR=%CD%\venv"
if exist "%VENV_DIR%\Scripts\python.exe" goto :existing_venv

if defined BASE_PYTHON goto :create_venv
for /f "delims=" %%P in ('py -3 -c "import struct, sys, venv, ensurepip; sys.exit(1) if not (sys.version_info >= (3, 11) and struct.calcsize('P') == 8) else print(sys.executable)" 2^>nul') do set "BASE_PYTHON=%%P"
if defined BASE_PYTHON goto :create_venv
for /f "delims=" %%P in ('python -c "import struct, sys, venv, ensurepip; sys.exit(1) if not (sys.version_info >= (3, 11) and struct.calcsize('P') == 8) else print(sys.executable)" 2^>nul') do set "BASE_PYTHON=%%P"
if defined BASE_PYTHON goto :create_venv
echo Python 3.11 or newer was not found.
if "%ALLOW_PYTHON_DOWNLOAD%"=="0" goto :python_download_disabled
echo Downloading a verified private Python runtime for LoomStroke...
if not exist "%INSTALL_ROOT%install_python.ps1" (
    echo ERROR: install_python.ps1 is missing. Extract the complete ZIP again.
    goto :done
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%INSTALL_ROOT%install_python.ps1" -RuntimeRoot "%CD%\.runtime"
if errorlevel 1 goto :command_error
set "BASE_PYTHON=%CD%\.runtime\python-3.13.16\tools\python.exe"
goto :create_venv

:python_download_disabled
echo Automatic Python download is disabled by --no-python-download.
echo Install 64-bit Python or run install.bat without that option.
goto :done

:create_venv
"%BASE_PYTHON%" -c "import struct, sys; sys.exit(0 if sys.version_info >= (3, 11) and struct.calcsize('P') == 8 else 1)"
if errorlevel 1 goto :python_error
echo Creating virtual environment...
"%BASE_PYTHON%" -m venv "%VENV_DIR%"
if errorlevel 1 goto :command_error

:existing_venv
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
if not exist "%VENV_PYTHON%" (
    echo ERROR: The existing .venv folder has no Python executable.
    echo Repair or rename that folder, then run install.bat again.
    goto :done
)
"%VENV_PYTHON%" -c "import struct, sys; sys.exit(0 if sys.version_info >= (3, 11) and struct.calcsize('P') == 8 else 1)"
if errorlevel 1 goto :python_error
echo Using "%VENV_PYTHON%"
echo Installing LoomStroke and its dependencies, including wgpu and google-genai...
set "PACKAGE=."
if "%WITH_NANOBANANA%"=="1" set "PACKAGE=.[nanobanana]"
"%VENV_PYTHON%" -m pip install -e "%PACKAGE%"
if errorlevel 1 goto :command_error
"%VENV_PYTHON%" -m pip check
if errorlevel 1 goto :command_error
echo.
echo Installation completed successfully.
echo Start LoomStroke with boot_LoomStroke.bat.
set "EXIT_CODE=0"
goto :done

:python_error
echo ERROR: A working 64-bit Python 3.11 or newer is required.
echo Existing environments are not deleted or replaced automatically.
goto :done

:command_error
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo ERROR: Installation failed with exit code %EXIT_CODE%.
echo Read the error above, then run install.bat again after fixing it.
goto :done

:help
echo Usage: install.bat [--nanobanana] [--python "C:\path\python.exe"] [--no-python-download] [--no-pause]
echo Creates .venv, or reuses an existing .venv or venv.
echo Includes NanoBanana dependencies by default. --nanobanana remains supported.
echo Downloads a verified private Python when no usable Python is found.
echo Requires 64-bit Windows and an internet connection. No administrator rights needed.
set "EXIT_CODE=0"
goto :finish

:usage_error
echo Usage: install.bat [--nanobanana] [--python "C:\path\python.exe"] [--no-python-download] [--no-pause]
set "EXIT_CODE=2"
goto :finish

:done
popd
:finish
if "%PAUSE_ON_EXIT%"=="1" pause
exit /b %EXIT_CODE%
