import interception
from interception.exceptions import DriverNotFoundError

import interception_driver

INSTALLER_HINT = (
    "Interception driver not installed/active. Installer is located at "
    r"Downloads\Interception-v1.0.1\command line installer\install-interception.exe "
    "(run as Administrator with /install, then reboot)."
)

REBOOT_HINT = (
    "Interception driver is installed according to the registry, but not (yet) active - "
    "a reboot after installation is probably missing. Please restart the computer."
)


class HidSender:
    """Sends a key like a real keyboard - goes to the focused window."""

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
                # Known failure mode of the interception-python library: if it finds no real
                # devices (e.g. because the driver is registered in the registry but not yet
                # actively loaded due to a missing reboot), its device detection runs past the
                # internally only partially populated device array and raises a raw IndexError
                # instead of a meaningful error.
                if interception_driver.is_installed():
                    raise RuntimeError(REBOOT_HINT) from exc
                raise RuntimeError(INSTALLER_HINT) from exc
            self._captured = True


class InputController:
    """Sends keys via the Interception HID driver, like a real second keyboard."""

    def __init__(self):
        self.sender = HidSender()

    def send(self, char):
        self.sender.send_key(char)

    def send_combo(self, key, modifiers=()):
        self.sender.send_combo(key, modifiers)
