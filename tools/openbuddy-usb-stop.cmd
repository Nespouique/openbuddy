@echo off
setlocal
set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
set "PYTHONPATH=%ROOT%"
"%PYTHON%" -m pc.opencode stop >nul 2>nul
exit /b 0