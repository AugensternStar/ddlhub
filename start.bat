@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")

if not exist .venv (
    echo [DDLHub] Creating virtual environment...
    %PY% -m venv .venv || goto :err
)
echo [DDLHub] Installing dependencies (first run takes 1-2 min, keep this window open)...
.venv\Scripts\python -m pip install --upgrade pip -q || goto :err
.venv\Scripts\python -m pip install -e . -q || goto :err
echo [DDLHub] Starting server. Browser will open automatically at http://127.0.0.1:8787
echo [DDLHub] Keep this window open. Press Ctrl+C here to stop the server.
.venv\Scripts\python -m ddlhub
goto :eof

:err
echo [DDLHub] FAILED. See the error above. This window stays open.
pause
