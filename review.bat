@echo off
rem Double-click me: opens the review window (works while the bot is running). Sell products is in there too.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto :nosetup
".venv\Scripts\python.exe" -m pipeline review
if errorlevel 1 pause
exit /b
:nosetup
echo The pipeline is not set up yet: double-click setup.bat first.
pause
