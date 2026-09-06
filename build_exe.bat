@echo off
rem Builds a self-contained FreeFlow (dist\FreeFlow\FreeFlow.exe) and zips it for sharing.
cd /d "%~dp0"
".venv\Scripts\python.exe" build_exe.py %*
pause
