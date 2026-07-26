import subprocess
import zipfile
from pathlib import Path

from audio_devices import list_speakers

VENDOR_DIR = Path(__file__).parent / "vendor" / "vbcable"
PACKAGE_ZIP = VENDOR_DIR / "VBCABLE_Driver_Pack45.zip"
EXTRACT_DIR = VENDOR_DIR / "extracted"
SETUP_EXE = EXTRACT_DIR / "VBCABLE_Setup_x64.exe"

# VB-CABLE ist Donationware, darf laut beiliegender readme.txt unverändert weiterverteilt
# werden ("copy and diffuse the VB-CABLE package AS IS"), aber NICHT automatisiert in eine
# eigene Installationsroutine eingebunden werden ("not allowed to integrate the VB-CABLE
# package in another software installation procedure without Author agreement"). Deshalb
# öffnen wir hier nur den unveränderten, offiziellen Installer - die eigentliche Install-/
# Uninstall-Entscheidung trifft der Nutzer selbst in dessen eigenem Fenster, es gibt auch
# keine dokumentierten Silent-Schalter dafür.


def is_installed():
    return any("cable" in speaker.name.lower() for speaker in list_speakers())


def open_installer():
    if not PACKAGE_ZIP.exists():
        raise RuntimeError(f"Paket nicht gefunden: {PACKAGE_ZIP}")
    if not SETUP_EXE.exists():
        EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(PACKAGE_ZIP) as zf:
            zf.extractall(EXTRACT_DIR)
        if not SETUP_EXE.exists():
            raise RuntimeError(f"{SETUP_EXE.name} nach Entpacken nicht gefunden.")

    command = (
        f'$p = Start-Process -FilePath "{SETUP_EXE}" -WorkingDirectory "{EXTRACT_DIR}" '
        f'-Verb RunAs -Wait -PassThru; exit $p.ExitCode'
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or f"VBCABLE_Setup_x64.exe fehlgeschlagen (Exit-Code {result.returncode})."
        )
