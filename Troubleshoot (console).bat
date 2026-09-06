@echo off
rem Runs FreeFlow with a console window and verbose logging. Only for troubleshooting - use the desktop shortcut normally.
"%~dp0.venv\Scripts\python.exe" "%~dp0FreeFlow.pyw" --debug
pause
