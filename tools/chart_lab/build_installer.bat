@echo off
REM Builds the chart_lab Windows executable via PyInstaller.
REM Used by both a human (double-click / run from this folder) and
REM .github\workflows\chart_lab_release.yml - the actual build logic lives here only, once,
REM so the workflow YAML has nothing to keep in sync with a locally-run build.
setlocal

cd /d "%~dp0"

if not exist .venv (
    py -m venv .venv
)
call .venv\Scripts\activate.bat

pip install -q -r requirements.txt
pip install -q pyinstaller

python -m PyInstaller chart_lab.spec --clean -y
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo.
echo Build complete: %~dp0dist\chart_lab\chart_lab.exe
