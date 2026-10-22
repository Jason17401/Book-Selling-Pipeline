@echo off
rem Double-click me: starts the Telegram bot. Keep this window open while you send photos.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto :nosetup
".venv\Scripts\python.exe" -m pipeline bot
pause
exit /b
:nosetup
echo The pipeline is not set up yet: double-click setup.bat first.
pause
