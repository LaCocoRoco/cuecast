import win32api
import win32con

import interception
from interception.exceptions import DriverNotFoundError

import interception_driver
import keys

INSTALLER_HINT = (
    "Interception-Treiber nicht installiert/aktiv. Installer liegt unter "
    r"Downloads\Interception-v1.0.1\command line installer\install-interception.exe "
    "(als Administrator mit /install ausführen, danach neu starten)."
)

REBOOT_HINT = (
    "Interception-Treiber ist laut Registry installiert, aber (noch) nicht aktiv - "
    "vermutlich fehlt der Neustart nach der Installation. Bitte Rechner neu starten."
)


class PostMessageSender:
    """Sendet eine Taste gezielt an ein Fenster-Handle - unabhängig vom Fokus."""

    def __init__(self, hwnd):
        self.hwnd = hwnd

    def send_key(self, char):
        vk = win32api.VkKeyScan(char) & 0xFF
        self._press(vk)

    def send_combo(self, key, modifiers=()):
        modifier_vks = [keys.VK_CODES[modifier.upper()] for modifier in modifiers]
        vk = keys.VK_CODES.get(key.upper())
        if vk is None:
            vk = win32api.VkKeyScan(key) & 0xFF
        for modifier_vk in modifier_vks:
            self._post_down(modifier_vk)
        self._press(vk)
        for modifier_vk in reversed(modifier_vks):
            self._post_up(modifier_vk)

    def _press(self, vk):
        self._post_down(vk)
        self._post_up(vk)

    def _post_down(self, vk):
        # lParam korrekt aufbauen (Repeat-Count=1, Scancode) - ein lParam von 0 ignorieren
        # manche Fensterprozeduren, da sie den Scancode daraus lesen statt sich nur auf
        # wParam (den VK-Code) zu verlassen.
        scan_code = win32api.MapVirtualKey(vk, 0)
        lparam = 1 | (scan_code << 16)
        win32api.PostMessage(self.hwnd, win32con.WM_KEYDOWN, vk, lparam)

    def _post_up(self, vk):
        scan_code = win32api.MapVirtualKey(vk, 0)
        lparam = 1 | (scan_code << 16) | (1 << 30) | (1 << 31)
        win32api.PostMessage(self.hwnd, win32con.WM_KEYUP, vk, lparam)


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
    """Hält genau einen aktiven Sender - ein Wechsel verwirft den vorherigen vollständig,
    sodass immer nur ein einziger Sende-Pfad erreichbar ist."""

    HID_VARIANT = "Human Interface Device"
    POST_MESSAGE_VARIANT = "Post Message"

    def __init__(self):
        self.sender = None
        self.variant = None

    def set_variant(self, variant, hwnd=None):
        if variant == self.POST_MESSAGE_VARIANT and hwnd is not None:
            self.sender = PostMessageSender(hwnd)
        elif variant == self.HID_VARIANT:
            self.sender = HidSender()
        else:
            self.sender = None
        self.variant = variant

    def send(self, char):
        if self.sender is None:
            raise RuntimeError("Kein Ziel gewählt.")
        self.sender.send_key(char)

    def send_combo(self, key, modifiers=()):
        if self.sender is None:
            raise RuntimeError("Kein Ziel gewählt.")
        self.sender.send_combo(key, modifiers)
