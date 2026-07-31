import interception
from interception.exceptions import DriverNotFoundError

import interception_driver

INSTALLER_HINT = (
    "Interception-Treiber nicht installiert/aktiv. Installer liegt unter "
    r"Downloads\Interception-v1.0.1\command line installer\install-interception.exe "
    "(als Administrator mit /install ausführen, danach neu starten)."
)

REBOOT_HINT = (
    "Interception-Treiber ist laut Registry installiert, aber (noch) nicht aktiv - "
    "vermutlich fehlt der Neustart nach der Installation. Bitte Rechner neu starten."
)


class HidSender:
    """Sendet eine Taste wie eine echte Tastatur - geht an das fokussierte Fenster."""

    def __init__(self):
        self._captured = False

    def send_key(self, char):
        self._ensure_captured()
        interception.press(char)

    def send_combo(self, key, modifiers=()):
        self._ensure_captured()
        self._press_with_modifiers(list(modifiers), key.lower())

    def _press_with_modifiers(self, modifiers, key):
        if not modifiers:
            interception.press(key)
            return
        modifier, *rest = modifiers
        with interception.hold_key(modifier):
            self._press_with_modifiers(rest, key)

    def _ensure_captured(self):
        if not self._captured:
            try:
                interception.auto_capture_devices(keyboard=True, mouse=False)
            except DriverNotFoundError as exc:
                raise RuntimeError(INSTALLER_HINT) from exc
            except IndexError as exc:
                # Bekannter Fehlerfall der interception-python-Bibliothek: findet sie keine
                # echten Geräte (z.B. weil der Treiber zwar in der Registry als installiert
                # eingetragen, aber mangels Neustart noch nicht aktiv geladen ist), läuft ihre
                # Geräte-Erkennung über das intern nur teilweise befüllte Geräte-Array hinaus
                # und wirft ein rohes IndexError statt eines aussagekräftigen Fehlers.
                if interception_driver.is_installed():
                    raise RuntimeError(REBOOT_HINT) from exc
                raise RuntimeError(INSTALLER_HINT) from exc
            self._captured = True


class InputController:
    """Sendet Tasten über den Interception-HID-Treiber, wie eine echte zweite Tastatur."""

    def __init__(self):
        self.sender = HidSender()

    def send(self, char):
        self.sender.send_key(char)

    def send_combo(self, key, modifiers=()):
        self.sender.send_combo(key, modifiers)
