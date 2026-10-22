@echo off
rem Double-click me to set everything up - once at the start, and again after every update (it is safe to repeat):
rem makes the Python environment (.venv), installs what the pipeline needs, makes your settings file (.env) and the
rem data folders, and runs the tests. Afterwards: fill in .env, then use bot.bat and review.bat.
setlocal
cd /d "%~dp0"
title Book Selling Pipeline - setup

rem ---- find Python (python, or the "py" launcher) ----
set "PY="
python --version >nul 2>nul && set "PY=python"
if not defined PY py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY goto :nopython

if exist ".venv\Scripts\python.exe" goto :haveenv
echo [1/6] Making the Python environment: .venv
%PY% -m venv .venv
if errorlevel 1 goto :failed
goto :activate
:haveenv
echo [1/6] The Python environment .venv is already there - using it
:activate
call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :failed

echo.
echo [2/6] Updating pip
python -m pip install --upgrade pip
if errorlevel 1 goto :failed

echo.
echo [3/6] Installing what the pipeline needs - a few minutes the first time
python -m pip install -r requirements-dev.txt
if errorlevel 1 goto :failed

echo.
set "NEWENV="
if exist ".env" goto :haveenvfile
echo [4/6] Making your settings file .env from .env.example
copy /y ".env.example" ".env" >nul
if errorlevel 1 goto :failed
set "NEWENV=1"
goto :folders
:haveenvfile
echo [4/6] Your settings file .env is already there - left as it is

:folders
echo.
echo [5/6] Making the data folders
python -m pipeline setup
if errorlevel 1 goto :failed

echo.
echo [6/6] Running the tests - about a minute
python -m pytest -q
if errorlevel 1 goto :testsfailed

echo.
echo ============================================================
echo  Setup done. Everything works.
goto :next

:testsfailed
echo.
echo ============================================================
echo  Setup done, but some tests FAILED - see the messages above.
echo  The pipeline is installed; send the messages if unsure.

:next
echo.
echo  Next:
echo    1. Fill in .env - at least TELEGRAM_BOT_TOKEN and ALLOWED_USER_IDS
echo       (TELEGRAM_SETUP.md says how; SETUP.md lists the optional keys).
echo    2. Double-click bot.bat to run the Telegram bot.
echo    3. Double-click review.bat to check the books and sell them.
echo ============================================================
if defined NEWENV start "" notepad ".env"
echo.
pause
exit /b 0

:nopython
echo Python was not found.
echo Install Python 3.11 or 3.12 from https://www.python.org/downloads/ and tick
echo "Add python.exe to PATH" in the installer. Then double-click setup.bat again.
echo.
pause
exit /b 1

:failed
echo.
echo ============================================================
echo  Setup stopped: the step above failed - see its message.
echo  Fix it (e.g. check the internet connection) and double-click setup.bat again.
echo ============================================================
echo.
pause
exit /b 1
