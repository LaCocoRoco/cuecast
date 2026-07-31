import subprocess
import winreg
from pathlib import Path

INSTALLER_PATH = Path(__file__).parent / "vendor" / "interception" / "install-interception.exe"

# Keyboard device class - the Interception filter registers itself there as "keyboard" in
# UpperFilters (see earlier manual install/uninstall in this conversation).
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
    # -PassThru + "exit $p.ExitCode" pass the real exit code of install-interception.exe
    # through to subprocess.run() - without -PassThru, "-Wait" would still wait, but the
    # elevated child process's exit code would be lost and result.returncode would only
    # reflect whether Start-Process itself managed to start (almost always 0), regardless of
    # whether the install/uninstall actually succeeded internally.
    # -WorkingDirectory sets the working directory to the exe's own folder, matching manual
    # execution (cd into the folder, then run install-interception.exe) - without it,
    # Start-Process inherits the calling (non-elevated) process's working directory, which
    # here is presumably the repo root instead of vendor\interception.
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
