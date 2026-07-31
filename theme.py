import tkinter as tk
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


def bind_tooltip(widget, text):
    """Shows a small dark-themed popup with `text` below `widget` on hover.

    ttk has no built-in tooltip widget, so this is a borderless Toplevel shown/hidden via
    <Enter>/<Leave>, positioned just below the widget it's bound to.
    """
    state = {"window": None}

    def show(event=None):
        if state["window"] is not None:
            return
        x = widget.winfo_rootx()
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        window = tk.Toplevel(widget)
        window.wm_overrideredirect(True)
        window.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            window, text=text, justify="left", background=BG_PANEL, foreground=FG,
            relief="solid", borderwidth=1, wraplength=320, padx=6, pady=4,
        )
        label.pack()
        state["window"] = window

    def hide(event=None):
        if state["window"] is not None:
            state["window"].destroy()
            state["window"] = None

    widget.bind("<Enter>", show)
    widget.bind("<Leave>", hide)
