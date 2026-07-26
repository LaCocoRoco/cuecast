import time
import tkinter as tk
from tkinter import ttk

from key_sender import InputController
from theme import BG_PANEL, FG, apply_dark_theme
from windows import list_windows

MIN_WIDTH = 560
MIN_HEIGHT = 240

VARIANTS = [InputController.POST_MESSAGE_VARIANT, InputController.HID_VARIANT]


class InputTester(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Input Tester")
        self.geometry(f"{MIN_WIDTH}x{MIN_HEIGHT}")
        self.minsize(MIN_WIDTH, MIN_HEIGHT)

        self.controller = InputController()
        self.windows = []

        apply_dark_theme(self)
        self._build_widgets()
        self._refresh_windows()

    def _build_widgets(self):
        row1 = ttk.Frame(self)
        row1.pack(side="top", fill="x", padx=8, pady=(8, 4))
        ttk.Label(row1, text="Variant:").pack(side="left")
        self.variant_var = tk.StringVar(value=VARIANTS[0])
        self.variant_combo = ttk.Combobox(
            row1, textvariable=self.variant_var, values=VARIANTS, state="readonly", width=16
        )
        self.variant_combo.pack(side="left", padx=4)
        self.variant_combo.bind("<<ComboboxSelected>>", lambda event: self._on_target_change())

        row2 = ttk.Frame(self)
        row2.pack(side="top", fill="x", padx=8, pady=4)
        ttk.Label(row2, text="Window:").pack(side="left")
        self.window_var = tk.StringVar()
        self.window_combo = ttk.Combobox(row2, textvariable=self.window_var, state="readonly", width=45)
        self.window_combo.pack(side="left", padx=4)
        self.window_combo.bind("<<ComboboxSelected>>", lambda event: self._on_target_change())
        ttk.Button(row2, text="Refresh", command=self._refresh_windows).pack(side="left", padx=4)

        row3 = ttk.Frame(self)
        row3.pack(side="top", fill="x", padx=8, pady=4)
        ttk.Label(row3, text="Key:").pack(side="left")
        self.key_var = tk.StringVar(value="a")
        ttk.Entry(row3, textvariable=self.key_var, width=4).pack(side="left", padx=4)
        ttk.Button(row3, text="Send", command=self._send).pack(side="left", padx=(16, 4))

        self.log_text = tk.Text(
            self, height=6, bg=BG_PANEL, fg=FG, insertbackground=FG,
            relief="flat", borderwidth=0, state="disabled", wrap="word",
        )
        self.log_text.pack(side="top", fill="both", expand=True, padx=8, pady=8)

    def _refresh_windows(self):
        self.windows = list_windows()
        labels = [f"{w.title} — {w.process_name}" for w in self.windows]
        self.window_combo.config(values=labels)
        self._on_target_change()

    def _on_target_change(self):
        variant = self.variant_var.get()
        is_post_message = variant == InputController.POST_MESSAGE_VARIANT
        self.window_combo.config(state="readonly" if is_post_message else "disabled")

        hwnd = None
        if is_post_message:
            index = self.window_combo.current()
            if index >= 0:
                hwnd = self.windows[index].hwnd
        self.controller.set_variant(variant, hwnd)

    def _send(self):
        char = self.key_var.get()
        if not char:
            self._log("Keine Taste konfiguriert.")
            return
        try:
            self.controller.send(char[0])
            self._log(f"'{char[0]}' via {self.variant_var.get()} gesendet.")
        except Exception as exc:
            self._log(f"Fehler: {exc}")

    def _log(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")


if __name__ == "__main__":
    InputTester().mainloop()
