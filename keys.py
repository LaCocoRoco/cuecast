# Anzeige-/Auswahlreihenfolge für das Haupttaste-Dropdown. Jeder Name ist so gewählt, dass
# name.lower() direkt ein gültiger interception-Tastenname ist (siehe key_sender.py).
MAIN_KEYS = (
    [chr(c) for c in range(ord("A"), ord("Z") + 1)]
    + [chr(c) for c in range(ord("0"), ord("9") + 1)]
    + [f"F{n}" for n in range(1, 13)]
    + ["Space", "Enter", "Escape", "Tab", "Up", "Down", "Left", "Right", "Backspace", "Delete"]
)
