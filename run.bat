@echo off
cd /d "%~dp0"

set "PYTHON_CMD="

py -3 --version >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"

if not defined PYTHON_CMD (
    python --version >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo Python wurde nicht gefunden.
    echo.
    echo Falls Python eigentlich installiert ist, wird stattdessen der
    echo Microsoft-Store-Platzhalter aufgerufen ^(Einstellungen -^> Apps -^>
    echo Erweiterte App-Einstellungen -^> App-Ausfuehrungsaliase -^> "python.exe"
    echo/"python3.exe" deaktivieren^), oder Python von python.org neu installieren
    echo ^(Haken bei "Add to PATH" setzen^).
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Erster Start - richte Umgebung ein, das kann einen Moment dauern...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo Erstellen der virtuellen Umgebung fehlgeschlagen.
        pause
        exit /b 1
    )
    echo Virtuelle Umgebung erstellt.
)

echo Pruefe/installiere Abhaengigkeiten - beim ersten Mal werden dabei ca. 100MB
echo heruntergeladen, das kann je nach Internetverbindung einige Minuten dauern...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Installation der Abhaengigkeiten fehlgeschlagen.
    pause
    exit /b 1
)
echo Fertig, starte Anwendung...

start "" ".venv\Scripts\pythonw.exe" "trigger_editor.py"
