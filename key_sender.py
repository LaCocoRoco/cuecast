import time

import win32api
import interception
from interception.exceptions import DriverNotFoundError

import interception_driver

# Virtual-key code for the left mouse button (used by wait_for_left_click).
VK_LBUTTON = 0x01

INSTALLER_HINT = (
    "Interception driver not installed/active. Installer is located at "
    r"Downloads\Interception-v1.0.1\command line installer\install-interception.exe "
    "(run as Administrator with /install, then reboot)."
)

REBOOT_HINT = (
    "Interception driver is installed according to the registry, but not (yet) active - "
    "a reboot after installation is probably missing. Please restart the computer."
)


def get_mouse_position():
    """Current cursor position as (x, y) - reads the OS cursor directly, does not need the
    Interception driver (unlike actually sending clicks/movement)."""
    return interception.mouse_position()


def wait_for_left_click(should_continue):
    """Blocks (polling every 10ms) until the left mouse button goes from up to down, then
    returns the cursor position at that moment as (x, y). Uses GetAsyncKeyState, which
    reports the physical button state system-wide regardless of which window has focus -
    unlike the Interception driver, this needs no capture/installation to just read state.

    should_continue is polled every iteration; return False from it to abort early (this
    then returns None instead of a position) - used to let a second "Set" click cancel a
    still-pending capture.
    """
    def is_down():
        return bool(win32api.GetAsyncKeyState(VK_LBUTTON) & 0x8000)

    # If the button happens to already be down (e.g. still the very click that triggered
    # this capture to start), wait for it to be released first, so that click isn't
    # immediately (mis)captured as the intended one.
    while is_down():
        if not should_continue():
            return None
        time.sleep(0.01)

    while not is_down():
        if not should_continue():
            return None
        time.sleep(0.01)

    return get_mouse_position()


class HidSender:
    """Sends a key/click like a real keyboard/mouse - goes to whatever is focused/under
    the cursor."""

    def __init__(self):
        self._captured = False

    def send_key(self, char):
        self._ensure_captured()
        interception.press(char)

    def send_combo(self, key, modifiers=()):
        self._ensure_captured()
        self._press_with_modifiers(list(modifiers), key.lower())

    def send_click(self, x, y, button="right"):
        self._ensure_captured()
        interception.click(x, y, button=button)

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
                interception.auto_capture_devices(keyboard=True, mouse=True)
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
    """Sends keys/clicks via the Interception HID driver, like a real second keyboard/mouse."""

    def __init__(self):
        self.sender = HidSender()

    def send(self, char):
        self.sender.send_key(char)

    def send_combo(self, key, modifiers=()):
        self.sender.send_combo(key, modifiers)

    def send_click(self, x, y, button="right"):
        self.sender.send_click(x, y, button)
