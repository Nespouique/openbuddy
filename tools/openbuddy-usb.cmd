@echo off
setlocal
set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
set "PYTHONPATH=%ROOT%"
set "LOGDIR=%LOCALAPPDATA%\openbuddy"
set "LOG=%LOGDIR%\bridge.log"

if not exist "%PYTHON%" (
  set "OB_MSG=Python environment not found: %PYTHON%"
  goto :popup
)
"%PYTHON%" -m pc.opencode status >nul 2>nul
if not errorlevel 1 (
  set "OB_MSG=OpenBuddy is already running."
  goto :popup
)

if not exist "%LOGDIR%" mkdir "%LOGDIR%"
powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath $env:PYTHON -ArgumentList '-m','pc.opencode','--serial','auto' -WindowStyle Hidden -RedirectStandardOutput $env:LOG -RedirectStandardError ($env:LOG + '.err')"
timeout /t 8 /nobreak >nul
"%PYTHON%" -m pc.opencode status >nul 2>nul
if not errorlevel 1 exit /b 0

powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; $t = ((Get-Content -Tail 6 -ErrorAction SilentlyContinue $env:LOG, ($env:LOG + '.err')) -join [Environment]::NewLine); [void][System.Windows.Forms.MessageBox]::Show('OpenBuddy did not start. Log: ' + $env:LOG + [Environment]::NewLine + $t, 'OpenBuddy')"
exit /b 1

:popup
powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; [void][System.Windows.Forms.MessageBox]::Show($env:OB_MSG, 'OpenBuddy')"
exit /b 1