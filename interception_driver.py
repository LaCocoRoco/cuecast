import subprocess
import winreg
from pathlib import Path

INSTALLER_PATH = Path(__file__).parent / "vendor" / "interception" / "install-interception.exe"

# Tastatur-Geräteklasse - der Interception-Filter trägt sich dort als "keyboard" in
# UpperFilters ein (siehe frühere manuelle Installation/Deinstallation in dieser Konversation).
_KEYBOARD_CLASS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4D36E96B-E325-11CE-BFC1-08002BE10318}"


def is_installed():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _KEYBOARD_CLASS_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "UpperFilters")
    except OSError:
        return False
    return "keyboard" in value


def install():
    _run_elevated("/install")


def uninstall():
    _run_elevated("/uninstall")


def _run_elevated(argument):
    if not INSTALLER_PATH.exists():
        raise RuntimeError(f"Installer nicht gefunden: {INSTALLER_PATH}")
    # -PassThru + "exit $p.ExitCode" reichen den echten Exit-Code von install-interception.exe
    # bis zu subprocess.run() durch - ohne -PassThru würde "-Wait" zwar warten, aber der
    # Exit-Code des elevierten Kindprozesses ginge verloren und result.returncode würde nur
    # widerspiegeln, ob Start-Process selbst starten konnte (fast immer 0), unabhängig davon
    # ob die Installation/Deinstallation intern tatsächlich erfolgreich war.
    # -WorkingDirectory setzt das Arbeitsverzeichnis auf den Ordner der exe selbst, wie bei
    # manueller Ausführung (cd in den Ordner, dann install-interception.exe aufrufen) - ohne
    # das erbt Start-Process das Arbeitsverzeichnis des aufrufenden (nicht-elevierten)
    # Prozesses, hier also vermutlich das Repo-Root statt vendor\interception.
    command = (
        f'$p = Start-Process -FilePath "{INSTALLER_PATH}" -ArgumentList "{argument}" '
        f'-WorkingDirectory "{INSTALLER_PATH.parent}" -Verb RunAs -Wait -PassThru; '
        f'exit $p.ExitCode'
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip()
            or f"install-interception.exe {argument} fehlgeschlagen (Exit-Code {result.returncode})."
        )
