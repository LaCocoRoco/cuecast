import win32con

# Anzeige-/Auswahlreihenfolge für das Haupttaste-Dropdown. Jeder Name ist so gewählt, dass
# name.lower() direkt ein gültiger interception-Tastenname ist (siehe key_sender.py).
MAIN_KEYS = (
    [chr(c) for c in range(ord("A"), ord("Z") + 1)]
    + [chr(c) for c in range(ord("0"), ord("9") + 1)]
    + [f"F{n}" for n in range(1, 13)]
    + ["Space", "Enter", "Escape", "Tab", "Up", "Down", "Left", "Right", "Backspace", "Delete"]
)

MODIFIERS = ["ctrl", "alt", "shift"]

VK_CODES = {
    **{chr(c): c for c in range(ord("A"), ord("Z") + 1)},
    **{chr(c): c for c in range(ord("0"), ord("9") + 1)},
    **{f"F{n}": getattr(win32con, f"VK_F{n}") for n in range(1, 13)},
    "SPACE": win32con.VK_SPACE,
    "ENTER": win32con.VK_RETURN,
    "ESCAPE": win32con.VK_ESCAPE,
    "TAB": win32con.VK_TAB,
    "UP": win32con.VK_UP,
    "DOWN": win32con.VK_DOWN,
    "LEFT": win32con.VK_LEFT,
    "RIGHT": win32con.VK_RIGHT,
    "BACKSPACE": win32con.VK_BACK,
    "DELETE": win32con.VK_DELETE,
    "WIN": win32con.VK_LWIN,
    "CTRL": win32con.VK_CONTROL,
    "ALT": win32con.VK_MENU,
    "SHIFT": win32con.VK_SHIFT,
}
