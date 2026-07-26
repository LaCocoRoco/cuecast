@echo off
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python wurde nicht gefunden. Bitte zuerst von python.org installieren ^(Haken bei "Add to PATH" setzen^).
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Erster Start - richte Umgebung ein...
    python -m venv .venv
)

".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo Installation der Abhaengigkeiten fehlgeschlagen.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" "trigger_editor.py"
