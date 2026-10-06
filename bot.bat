@echo off
rem Double-click me: starts the Telegram bot. Keep this window open while you send photos.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pipeline bot
pause
