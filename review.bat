@echo off
rem Double-click me: opens the review window (works while the bot is running).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pipeline review
if errorlevel 1 pause
