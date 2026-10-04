@echo off
REM Double-click launcher: creates a private venv on first run, installs requirements, starts chart_lab.
REM Needs Python 3.10+ (the "py" launcher) and the WHOLE easy-OTP repo (chart_lab imports sibling tools/ folders).
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python not found. Install Python 3.10+ from https://www.python.org/downloads/ ^(tick "py launcher"^), then run this again.
    pause
    exit /b 1
)

if not exist .venv\Scripts\activate.bat (
    py -m venv .venv || (pause & exit /b 1)
)
call .venv\Scripts\activate.bat

pip install -q -r requirements.txt || (echo Install failed. & pause & exit /b 1)

echo Starting chart_lab at http://127.0.0.1:7860 - keep this window open.
python -m chart_lab.app
pause
