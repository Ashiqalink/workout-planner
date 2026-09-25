@echo off
rem FitTrack launcher for Windows: makes the virtualenv on first run,
rem installs requirements when they change, starts the app and opens the browser.
setlocal
cd /d "%~dp0"

if exist "venv\Scripts\python.exe" goto install
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
echo Creating virtual environment...
%PY% -m venv venv
if errorlevel 1 (
    echo Python 3.10 or newer is needed: https://www.python.org/downloads/
    pause
    exit /b 1
)

:install
rem Reinstall only when requirements.txt differs from the copy saved last time.
fc /b requirements.txt venv\requirements.installed >nul 2>nul || (
    echo Installing requirements...
    venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt || (
        pause
        exit /b 1
    )
    copy /y requirements.txt venv\requirements.installed >nul
)

if not defined FITTRACK_MODE set "FITTRACK_MODE=local"
set "FITTRACK_OPEN_BROWSER=1"
venv\Scripts\python.exe app.py
if errorlevel 1 pause
