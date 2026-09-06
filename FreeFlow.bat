@echo off
rem Starts FreeFlow in the background (tray icon). The desktop shortcut or FreeFlow.vbs do the same without this console flash.
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0FreeFlow.pyw"
