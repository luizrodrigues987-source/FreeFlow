@echo off
rem One-time setup: creates the Python environment and installs all dependencies.
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3.10) || (set PY=python)
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PY% -m venv .venv || (echo Python 3.10+ is required. & pause & exit /b 1)
)
echo Installing dependencies (this downloads ~2 GB incl. CUDA libraries)...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Install failed. & pause & exit /b 1)
echo.
echo Done. Start FreeFlow with FreeFlow.bat
pause
