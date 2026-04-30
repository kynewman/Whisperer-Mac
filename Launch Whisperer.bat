@echo off
cd /d "%~dp0"
set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe"
if not exist "%PYTHON%" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
start "" "%PYTHON%" "%CD%\launcher.py"
