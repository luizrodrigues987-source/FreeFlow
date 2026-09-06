@echo off
rem Builds a small code-only update zip (dist\FreeFlow-update-r<revision>.zip, copied to the Desktop) to send to friends.
cd /d "%~dp0"
".venv\Scripts\python.exe" build_update.py
pause
