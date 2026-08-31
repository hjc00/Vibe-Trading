@echo off
setlocal
title Vibe-Trading Web Server

rem ============================================================
rem  One-click launcher for the Vibe-Trading web UI.
rem  Portable: it auto-detects its own folder, so you can copy
rem  the whole project to another machine and this still works.
rem ============================================================

rem ---- Locate the project root (the folder this script is in) ----
set "ROOT=%~dp0"
cd /d "%ROOT%"

rem ---- PyPI mirror. If you are NOT in China, delete "-i ..." below ----
set "PIP_INDEX=-i https://pypi.tuna.tsinghua.edu.cn/simple"

rem ---- Port. Default 8899. Override:  start-web.bat 8899 ----
if "%~1"=="" (set "PORT=8899") else (set "PORT=%~1")

rem ---- 1) Ensure the virtual environment exists ----
if exist ".venv\Scripts\vibe-trading.exe" goto env_ready

echo [setup] Virtual environment not found. Bootstrapping...
echo(

set "PYTHON="
set "PYVER="

rem Prefer an explicitly versioned interpreter so a stray old 3.x on PATH
rem (e.g. 3.8) is never picked ahead of a modern one. uv, python.org and
rem the py launcher all install one of these names.
for %%P in (python3.13 python3.12 python3.11) do (
    if not defined PYTHON where %%P >nul 2>nul && set "PYTHON=%%P"
)
if not defined PYTHON where py >nul 2>nul && set "PYTHON=py -3"
if not defined PYTHON where python >nul 2>nul && set "PYTHON=python"

if not defined PYTHON (
    echo [error] Python 3.11+ not found on PATH.
    echo         Install it from https://www.python.org/downloads/
    echo         and check "Add python.exe to PATH" during install.
    pause
    exit /b 1
)

rem Verify the interpreter is actually 3.11+ (a bare `python` may be old).
for /f "delims=" %%V in ('%PYTHON% -c "import sys; print(sys.version_info[0]*100 + sys.version_info[1])" 2^>nul') do set "PYVER=%%V"
if not defined PYVER (
    echo [error] Could not determine the version of: %PYTHON%
    pause
    exit /b 1
)
if %PYVER% LSS 311 (
    echo [error] Python 3.11+ is required, but %PYTHON% is too old.
    echo         Install Python 3.11+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [setup] Using interpreter: %PYTHON%
echo [setup] Creating virtual environment...
%PYTHON% -m venv .venv
if errorlevel 1 goto fail_venv

echo [setup] Installing dependencies (first run, a few minutes)...
".venv\Scripts\python.exe" -m pip install --upgrade pip %PIP_INDEX%
".venv\Scripts\python.exe" -m pip install -e ".[dev]" %PIP_INDEX%
if errorlevel 1 goto fail_deps

echo [setup] Bootstrap complete.
echo(

:env_ready

rem ---- 2) Ensure agent/.env exists ----
if not exist "agent\.env" (
    echo [warning] agent\.env not found.
    echo           Copy  agent\.env.example  to  agent\.env
    echo           and fill in your LLM API key, e.g. DEEPSEEK_API_KEY.
    echo           Then run this script again.
    pause
    exit /b 1
)

rem ---- 3) Launch the server ----
echo(
echo ======================================================
echo   Vibe-Trading Web UI  -  http://localhost:%PORT%
echo   Close this window to stop the server.
echo ======================================================
echo(

rem open the browser a few seconds later, once the server is up
start "" /min cmd /c "ping -n 4 127.0.0.1 >nul & start http://localhost:%PORT%"

".venv\Scripts\vibe-trading.exe" serve --port %PORT%

pause
exit /b 0

:fail_venv
echo [error] Failed to create the virtual environment.
pause
exit /b 1

:fail_deps
echo [error] Failed to install dependencies.
pause
exit /b 1
