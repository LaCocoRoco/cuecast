"""Minimales Zielfenster zum Testen von PostMessage/HID - im Gegensatz zum modernen
Windows-11-Notepad (WinUI3, kein klassisches Win32-Fenster) ist dies ein echtes
klassisches Win32-Fenster und verarbeitet injizierte Tastenevents zuverlässig."""

import tkinter as tk

from theme import BG, BG_PANEL, FG, apply_dark_theme

root = tk.Tk()
root.title("Test-Zielfenster")
root.geometry("400x120")
apply_dark_theme(root)

tk.Label(root, text="Hier sollte der gesendete Text ankommen:", bg=BG, fg=FG).pack(padx=12, pady=(16, 4))
entry = tk.Entry(
    root, font=("Segoe UI", 14), justify="center",
    bg=BG_PANEL, fg=FG, insertbackground=FG, relief="flat",
)
entry.pack(padx=12, pady=4, fill="x")
entry.focus_set()

root.mainloop()
