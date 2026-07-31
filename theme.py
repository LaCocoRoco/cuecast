from tkinter import ttk

# Based on VS Code "Dark Modern", deliberately not pure black.
BG = "#1f1f1f"
BG_PANEL = "#2d2d2d"
FG = "#cccccc"
BORDER = "#3c3c3c"
ACCENT = "#0078d4"
ACCENT_ACTIVE = "#1b8ad1"
WAVE_COLOR = "#4fc1ff"
SELECTION_COLOR = "#0078d4"
MARKER_COLOR = "#d7ba7d"
PLAYHEAD_COLOR = "#f14c4c"


def apply_dark_theme(root):
    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=FG, bordercolor=BORDER, darkcolor=BG_PANEL, lightcolor=BG_PANEL)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("TButton", background=BG_PANEL, foreground=FG, bordercolor=BORDER, focuscolor=ACCENT, padding=4)
    style.map("TButton", background=[("active", ACCENT_ACTIVE), ("pressed", ACCENT)])
    style.configure("TCombobox", fieldbackground=BG_PANEL, background=BG_PANEL, foreground=FG, arrowcolor=FG, bordercolor=BORDER)
    style.map("TCombobox", fieldbackground=[("readonly", BG_PANEL)], foreground=[("readonly", FG)])
    style.configure("TSpinbox", fieldbackground=BG_PANEL, background=BG_PANEL, foreground=FG, arrowcolor=FG, bordercolor=BORDER)
    style.configure("TEntry", fieldbackground=BG_PANEL, background=BG_PANEL, foreground=FG, insertcolor=FG, bordercolor=BORDER)
    style.configure("TCheckbutton", background=BG, foreground=FG, focuscolor=ACCENT)
    style.map("TCheckbutton", background=[("active", BG)], indicatorcolor=[("selected", ACCENT), ("!selected", BG_PANEL)])
    root.option_add("*TCombobox*Listbox.background", BG_PANEL)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", FG)
