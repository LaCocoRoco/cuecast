import random
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import simpledialog, ttk

import numpy as np
import winsound
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.widgets import SpanSelector
from scipy.io import wavfile

import interception_driver
import keys
import vbcable_driver
from audio_devices import list_speakers, resolve_speaker
from key_sender import InputController
from matcher import LiveMonitor, compute_peak_db
from record_snippet import SNIPPET_DIR, record, save_wav
from settings import load_settings, save_settings
from theme import (
    ACCENT,
    BG,
    BG_PANEL,
    BORDER,
    FG,
    MARKER_COLOR,
    PLAYHEAD_COLOR,
    SELECTION_COLOR,
    WAVE_COLOR,
    apply_dark_theme,
)

PREVIEW_PATH = Path(__file__).parent / "_preview.wav"

# Klicks (keine echte Auswahl) sind kürzer als das hier, in Sekunden. War vorher 0.08s -
# das hat aber echte, kurze Markierungen (z.B. kurze Splash-Transienten < 80ms) fälschlich
# als Klick statt als Auswahl behandelt. 0.03s ist ein Kompromiss zwischen beidem.
CLICK_EPSILON = 0.03
PLAYHEAD_INTERVAL_MS = 30

# Sicherheitspolster (fixe dB-Differenz, keine Prozentangabe - siehe compute_peak_db) unter
# dem gemessenen Peak einer markierten Auswahl, wenn sie per Button als Threshold übernommen
# wird.
THRESHOLD_SUGGESTION_MARGIN_DB = 3.0

# Mindest-/Standardgröße des Fensters (Pixel). Muss mindestens so groß sein wie der
# tatsächliche Platzbedarf der Steuerelemente (siehe winfo_reqwidth/reqheight).
MIN_WIDTH = 700
MIN_HEIGHT = 460

# Einheitliche Breiten, damit Dropdowns bzw. Buttons in derselben Spalte gleich groß sind.
COMBO_WIDTH = 24
BUTTON_WIDTH = 11

# Einheitliche Zeilenhöhe für alle Steuerzeilen (nicht Terminal/Wellenform), damit nichts
# je nach Zeileninhalt (Button vs. Entry vs. Checkbutton) leicht auf und ab springt.
ROW_HEIGHT = 33

# Feste Höhe der Wellenform-Anzeige (Pixel) - ändert sich nicht mit der Fenstergröße.
WAVEFORM_HEIGHT = 100

# ============================================================================
# Angel-Trigger-Timing - bewusst alles hier an einer Stelle zum Feinjustieren im Code, nicht
# über UI/Settings (siehe _run_angel_trigger/_check_angel_trigger_timeout für die Verwendung).
# ============================================================================
# Wartezeit (Sekunden, zufällig aus diesem Bereich) vor dem Fangen-Signal, nachdem ein Biss
# erkannt wurde - wirkt menschlicher als eine exakte Reaktionszeit.
ANGEL_TRIGGER_FIRST_DELAY_RANGE = (0.0, 1.0)
# Feste Pause zwischen Fangen-Signal und erneutem Auswerfen.
ANGEL_TRIGGER_FIXED_DELAY = 1.5
# Wartezeit (zufällig) vor dem erneuten Auswerfen-Signal.
ANGEL_TRIGGER_SECOND_DELAY_RANGE = (0.0, 1.0)
# Timeout selbst ist über "Timeout (s)" in der Oberfläche einstellbar (self.angel_timeout_var).
# Wie oft der Timeout geprüft wird - keine Zeitkonstante des Ablaufs selbst, muss i.d.R.
# nicht angepasst werden.
ANGEL_TRIGGER_TIMEOUT_CHECK_MS = 1000
# Bei Start wird nicht auf den ersten echten Biss gewartet - die Routine läuft stattdessen
# bereits nach dieser kurzen Anlaufzeit erstmals an (siehe _toggle_monitoring).
ANGEL_TRIGGER_INITIAL_DELAY_SECONDS = 5.0

# Ist Attack Trigger aktiviert, wird bei einem Timeout (vermutlich durch einen Angriff
# unterbrochenes Fischen) nicht nur einmal, sondern in diesem Intervall wiederholt
# angegriffen, bis wieder ein echter Biss (Threshold) erkannt wird - siehe _run_attack_loop.
ATTACK_TRIGGER_INTERVAL_SECONDS = 2.0

# Wie oft geprüft wird, ob das Lure-Intervall abgelaufen ist (siehe _check_lure_timer) - keine
# Zeitkonstante des Ablaufs selbst, muss i.d.R. nicht angepasst werden.
LURE_TIMER_CHECK_MS = 1000

# Cooldown ist bewusst fix (keine UI/Settings) - Zeit zwischen zwei Live-Erkennungstreffern.
COOLDOWN_SECONDS = 2.0


class TriggerEditor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Trigger Editor")
        self.geometry(f"{MIN_WIDTH}x{MIN_HEIGHT}")
        self.minsize(MIN_WIDTH, MIN_HEIGHT)

        self.speakers = list_speakers()
        self.audio = None
        self.sample_rate = None
        self.selection = None
        self.play_start = None
        self.stop_event = None
        self._press_x = None

        self.marker_line = None
        self.playhead_line = None
        self.is_playing = False
        self.is_paused = False
        self.active_range = None  # (start_sample, end_sample) der laufenden Wiedergabe
        self.paused_at = None  # Sekunden-Position bei Pause
        self.playhead_job = None
        self.playback_started_at = None
        self.playback_offset = 0.0
        self.playback_duration = 0.0

        self.monitor = None
        self.last_cast_at = None
        self.lure_last_used_at = None
        self.is_attacking = False

        # Trigger-Zähler/Laufzeit: werden über settings.json persistiert (siehe
        # _load_settings/_save_settings), Zurücksetzen nur explizit über den Reset-Button.
        self.trigger_count = 0
        self.total_runtime_seconds = 0.0
        self.session_started_at = None  # perf_counter()-Zeitpunkt des laufenden Start-Stop-Abschnitts

        self.input_controller = InputController()

        apply_dark_theme(self)
        self._build_widgets()
        self._load_settings()
        self._refresh_file_list()
        self._refresh_hid_status()
        self._refresh_vbcable_status()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_widgets(self):
        # Gerät/Snippets-Zeilen als gemeinsames Raster, damit Spalten
        # (Auswahl, Play, Stop, Löschen, Umbenennen, ...) über die Zeilen hinweg fluchten.
        controls = ttk.Frame(self)
        controls.pack(side="top", fill="x", padx=8, pady=8)
        for row in range(2):
            controls.grid_rowconfigure(row, minsize=ROW_HEIGHT)

        # Zeile 0: Aufnahmegerät + Record/Stop
        ttk.Label(controls, text="Device:").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=2)
        device_names = [s.name for s in self.speakers]
        self.device_combo = ttk.Combobox(controls, values=device_names, state="readonly", width=COMBO_WIDTH)
        if device_names:
            self.device_combo.current(0)
        self.device_combo.grid(row=0, column=1, sticky="w", padx=4, pady=2)
        self.device_combo.bind("<<ComboboxSelected>>", lambda event: self._on_device_change())
        self.record_button = ttk.Button(controls, text="Record", width=BUTTON_WIDTH, command=self._toggle_recording)
        self.record_button.grid(row=0, column=2, sticky="w", padx=4, pady=2)

        # VB-CABLE (virtuelles Audiogerät für Rechner ohne echte Soundkarte): nur Status +
        # Link zur offiziellen Download-Seite, damit der Treiber immer aktuell bleibt
        # (siehe vbcable_driver.py) - kein gebündelter/lokal installierter Installer.
        ttk.Label(controls, text="VB-CABLE:").grid(row=0, column=3, sticky="w", padx=(16, 4), pady=2)
        self.vbcable_status_var = tk.StringVar(value="?")
        ttk.Label(controls, textvariable=self.vbcable_status_var).grid(row=0, column=4, sticky="w", padx=4, pady=2)
        self.vbcable_button = ttk.Button(
            controls, text="Download", width=BUTTON_WIDTH, command=self._open_vbcable_download_page
        )
        self.vbcable_button.grid(row=0, column=5, sticky="w", padx=4, pady=2)

        # Zeile 1: Snippet-Auswahl + Play/Stop/Löschen/Umbenennen/Speichern
        ttk.Label(controls, text="Snippets:").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=2)
        self.file_var = tk.StringVar()
        self.file_combo = ttk.Combobox(controls, textvariable=self.file_var, state="readonly", width=COMBO_WIDTH)
        self.file_combo.grid(row=1, column=1, sticky="w", padx=4, pady=2)
        self.file_combo.bind("<<ComboboxSelected>>", lambda event: self._load_selected_file())

        self.play_button = ttk.Button(controls, text="Play", width=BUTTON_WIDTH, command=self._toggle_play_pause)
        self.play_button.grid(row=1, column=2, sticky="w", padx=4, pady=2)
        ttk.Button(controls, text="Stop", width=BUTTON_WIDTH, command=self._stop_playback).grid(row=1, column=3, sticky="w", padx=4, pady=2)
        ttk.Button(controls, text="Delete", width=BUTTON_WIDTH, command=self._delete_snippet).grid(row=1, column=4, sticky="w", padx=4, pady=2)
        ttk.Button(controls, text="Rename", width=BUTTON_WIDTH, command=self._rename_snippet).grid(row=1, column=5, sticky="w", padx=4, pady=2)

        # Wellenform: feste Größe (Breite = Grid-Breite, Höhe = WAVEFORM_HEIGHT), wächst/
        # verschiebt sich nicht mit der Fenstergröße. Bewusst hier (direkt unter den
        # Aufnahme-Zeilen), um es optisch von den generellen Einstellungen darunter zu trennen.
        self.figure = Figure(facecolor=BG)
        self.axes = self.figure.add_subplot(111)
        self._style_axes()
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        canvas_widget = self.canvas.get_tk_widget()
        canvas_widget.pack(side="top", anchor="w", padx=8, pady=8)
        self.canvas.mpl_connect("button_press_event", self._on_press)
        self.canvas.mpl_connect("button_release_event", self._on_release)

        self.span_selector = SpanSelector(
            self.axes,
            self._on_select,
            "horizontal",
            useblit=True,
            interactive=True,
            minspan=CLICK_EPSILON,
            props=dict(alpha=0.3, facecolor=SELECTION_COLOR),
        )

        # Zeigt den lautesten Block (gleiche Blockgröße wie die Live-Erkennung, siehe
        # matcher.compute_peak_db) der per Maus gezogenen Auswahl in dBFS - der Button
        # übernimmt Peak minus Sicherheitspolster direkt als Threshold.
        selection_row = ttk.Frame(self)
        selection_row.pack(side="top", fill="x", padx=8)
        self.selection_db_var = tk.StringVar(value="")
        ttk.Label(selection_row, textvariable=self.selection_db_var).pack(side="left")
        self.apply_threshold_button = ttk.Button(
            selection_row, text="Als Threshold übernehmen", command=self._apply_selection_as_threshold
        )
        self.apply_threshold_button.pack(side="left", padx=(8, 0))

        # HID (Interception-Treiber, geht immer an das fokussierte Fenster) - Status +
        # Install/Uninstall + schneller manueller Test.
        input_frame = ttk.Frame(self, height=ROW_HEIGHT)
        input_frame.pack_propagate(False)
        input_frame.pack(side="top", fill="x", padx=8, pady=4)

        self.hid_status_var = tk.StringVar(value="HID Driver: ?")
        ttk.Label(input_frame, textvariable=self.hid_status_var).pack(side="left")
        self.hid_driver_button = ttk.Button(
            input_frame, text="Install", width=BUTTON_WIDTH, command=self._toggle_hid_driver
        )
        self.hid_driver_button.pack(side="left", padx=4)

        # Sendet die Windows-Taste (leicht sichtbar: öffnet/schließt das Startmenü), um HID
        # unabhängig von den unten konfigurierten Signalen schnell zu testen.
        ttk.Button(input_frame, text="Test", width=BUTTON_WIDTH, command=self._test_input_manager).pack(
            side="left", padx=(16, 4)
        )

        # Angel Trigger (Zeile 0) + Lure Trigger (Zeile 1): gemeinsames Grid, damit
        # Checkbuttons/Dropdowns beider Zeilen exakt untereinander ausgerichtet sind (wie bei
        # Device/Snippets oben).
        trigger_frame = ttk.Frame(self)
        trigger_frame.pack(side="top", fill="x", padx=8, pady=(0, 4))
        for row in range(3):
            trigger_frame.grid_rowconfigure(row, minsize=ROW_HEIGHT)

        # Zeile 0: Angel Trigger - Signal, das bei einem erkannten Biss (Audio-Treffer)
        # gesendet wird, siehe _run_angel_trigger. Timeout (s): siehe _check_angel_trigger_timeout.
        ttk.Label(trigger_frame, text="Angel Trigger:").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=2)
        self.mod_ctrl_var = tk.BooleanVar(value=False)
        self.mod_alt_var = tk.BooleanVar(value=False)
        self.mod_shift_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(trigger_frame, text="Ctrl", variable=self.mod_ctrl_var, command=self._save_settings).grid(row=0, column=1, sticky="w", padx=4, pady=2)
        ttk.Checkbutton(trigger_frame, text="Alt", variable=self.mod_alt_var, command=self._save_settings).grid(row=0, column=2, sticky="w", padx=4, pady=2)
        ttk.Checkbutton(trigger_frame, text="Shift", variable=self.mod_shift_var, command=self._save_settings).grid(row=0, column=3, sticky="w", padx=4, pady=2)

        self.main_key_var = tk.StringVar(value="F6")
        self.main_key_combo = ttk.Combobox(
            trigger_frame, textvariable=self.main_key_var, values=keys.MAIN_KEYS, state="readonly", width=8
        )
        self.main_key_combo.grid(row=0, column=4, sticky="w", padx=4, pady=2)
        self.main_key_combo.bind("<<ComboboxSelected>>", lambda event: self._save_settings())

        ttk.Label(trigger_frame, text="Timeout (s):").grid(row=0, column=5, sticky="w", padx=(16, 4), pady=2)
        self.angel_timeout_var = tk.DoubleVar(value=22.0)
        angel_timeout_spinbox = ttk.Spinbox(
            trigger_frame, from_=5.0, to=60.0, increment=1.0, textvariable=self.angel_timeout_var, width=6
        )
        angel_timeout_spinbox.grid(row=0, column=6, sticky="w", padx=4, pady=2)
        for event in ("<FocusOut>", "<Return>", "<<Increment>>", "<<Decrement>>"):
            angel_timeout_spinbox.bind(event, lambda e: self._save_settings())

        # Zeile 1: Lure Trigger - gleicher Aufbau wie Angel Trigger, aber eigenes Signal, das
        # automatisch nach Ablauf des Intervalls (Minuten) erneut gesendet wird, um den Köder
        # aufzufrischen - siehe _check_lure_timer. 0 Minuten schaltet das Intervall ab.
        ttk.Label(trigger_frame, text="Lure Trigger:").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=2)
        self.lure_mod_ctrl_var = tk.BooleanVar(value=False)
        self.lure_mod_alt_var = tk.BooleanVar(value=False)
        self.lure_mod_shift_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(trigger_frame, text="Ctrl", variable=self.lure_mod_ctrl_var, command=self._save_settings).grid(row=1, column=1, sticky="w", padx=4, pady=2)
        ttk.Checkbutton(trigger_frame, text="Alt", variable=self.lure_mod_alt_var, command=self._save_settings).grid(row=1, column=2, sticky="w", padx=4, pady=2)
        ttk.Checkbutton(trigger_frame, text="Shift", variable=self.lure_mod_shift_var, command=self._save_settings).grid(row=1, column=3, sticky="w", padx=4, pady=2)

        self.lure_main_key_var = tk.StringVar(value="F7")
        self.lure_main_key_combo = ttk.Combobox(
            trigger_frame, textvariable=self.lure_main_key_var, values=keys.MAIN_KEYS, state="readonly", width=8
        )
        self.lure_main_key_combo.grid(row=1, column=4, sticky="w", padx=4, pady=2)
        self.lure_main_key_combo.bind("<<ComboboxSelected>>", lambda event: self._save_settings())

        ttk.Label(trigger_frame, text="Minutes:").grid(row=1, column=5, sticky="w", padx=(16, 4), pady=2)
        self.lure_interval_var = tk.DoubleVar(value=10.0)
        lure_interval_spinbox = ttk.Spinbox(
            trigger_frame, from_=0.0, to=60.0, increment=1.0, textvariable=self.lure_interval_var, width=6
        )
        lure_interval_spinbox.grid(row=1, column=6, sticky="w", padx=4, pady=2)
        for event in ("<FocusOut>", "<Return>", "<<Increment>>", "<<Decrement>>"):
            lure_interval_spinbox.bind(event, lambda e: self._save_settings())

        # Zeile 2: Attack Trigger - gleicher Aufbau wie Angel/Lure Trigger. Ist Enable aktiv,
        # wird dieses Signal beim Angel-Trigger-Timeout (siehe _check_angel_trigger_timeout)
        # zuerst gesendet, bevor die Angel erneut ausgeworfen wird (z.B. um einen aus dem
        # Wasser gesprungenen Gegner zuerst zu bekämpfen).
        ttk.Label(trigger_frame, text="Attack Trigger:").grid(row=2, column=0, sticky="w", padx=(0, 4), pady=2)
        self.attack_mod_ctrl_var = tk.BooleanVar(value=False)
        self.attack_mod_alt_var = tk.BooleanVar(value=False)
        self.attack_mod_shift_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(trigger_frame, text="Ctrl", variable=self.attack_mod_ctrl_var, command=self._save_settings).grid(row=2, column=1, sticky="w", padx=4, pady=2)
        ttk.Checkbutton(trigger_frame, text="Alt", variable=self.attack_mod_alt_var, command=self._save_settings).grid(row=2, column=2, sticky="w", padx=4, pady=2)
        ttk.Checkbutton(trigger_frame, text="Shift", variable=self.attack_mod_shift_var, command=self._save_settings).grid(row=2, column=3, sticky="w", padx=4, pady=2)

        self.attack_main_key_var = tk.StringVar(value="F8")
        self.attack_main_key_combo = ttk.Combobox(
            trigger_frame, textvariable=self.attack_main_key_var, values=keys.MAIN_KEYS, state="readonly", width=8
        )
        self.attack_main_key_combo.grid(row=2, column=4, sticky="w", padx=4, pady=2)
        self.attack_main_key_combo.bind("<<ComboboxSelected>>", lambda event: self._save_settings())

        self.attack_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            trigger_frame, text="Enable", variable=self.attack_enabled_var, command=self._save_settings
        ).grid(row=2, column=5, sticky="w", padx=(16, 4), pady=2)

        # Live-Erkennung: Threshold/Start + Log
        monitor_frame = ttk.Frame(self, height=ROW_HEIGHT)
        monitor_frame.pack_propagate(False)
        monitor_frame.pack(side="top", fill="x", padx=8, pady=(0, 8))

        ttk.Label(monitor_frame, text="Threshold (dB):").pack(side="left")
        self.threshold_var = tk.DoubleVar(value=-40.0)
        threshold_spinbox = ttk.Spinbox(
            monitor_frame, from_=-80.0, to=0.0, increment=1.0, textvariable=self.threshold_var, width=6
        )
        threshold_spinbox.pack(side="left", padx=4)
        for event in ("<FocusOut>", "<Return>", "<<Increment>>", "<<Decrement>>"):
            threshold_spinbox.bind(event, lambda e: self._on_monitor_settings_changed())

        self.monitor_button = ttk.Button(monitor_frame, text="Start", width=BUTTON_WIDTH, command=self._toggle_monitoring)
        self.monitor_button.pack(side="left", padx=(16, 4))

        # Trigger-Zähler + Laufzeit: über settings.json persistiert (self.trigger_count/
        # self.total_runtime_seconds werden bereits in __init__ initialisiert/geladen).
        self.trigger_count_var = tk.StringVar(value="Triggers: 0")
        ttk.Label(monitor_frame, textvariable=self.trigger_count_var).pack(side="left", padx=(16, 4))

        # Laufzeit zählt nur, solange die Erkennung aktiv ist (Start gedrückt), pausiert bei
        # Stop - siehe _update_runtime_display/_toggle_monitoring.
        self.runtime_var = tk.StringVar(value="Runtime: 0h 00m")
        ttk.Label(monitor_frame, textvariable=self.runtime_var).pack(side="left", padx=(8, 4))

        ttk.Button(monitor_frame, text="Reset", width=BUTTON_WIDTH, command=self._reset_counters).pack(
            side="left", padx=(8, 4)
        )

        self.log_text = tk.Text(
            self, height=6, bg=BG_PANEL, fg=FG, insertbackground=FG,
            relief="flat", borderwidth=0, state="disabled", wrap="none",
        )
        self.log_text.pack(side="top", fill="x", padx=8, pady=(0, 8))

        self.update_idletasks()
        grid_width = max(
            controls.winfo_reqwidth(),
            input_frame.winfo_reqwidth(),
            trigger_frame.winfo_reqwidth(),
            monitor_frame.winfo_reqwidth(),
        )
        canvas_widget.config(width=grid_width, height=WAVEFORM_HEIGHT)

    def _style_axes(self):
        self.axes.set_facecolor(BG_PANEL)
        for spine in self.axes.spines.values():
            spine.set_color(BORDER)
        self.axes.set_xticks([])
        self.axes.set_yticks([])

    # --- Aufnahme ---
    def _toggle_recording(self):
        if self.stop_event is not None:
            self.stop_event.set()
            return

        index = self.device_combo.current()
        speaker = resolve_speaker(index if index >= 0 else None)
        self.stop_event = threading.Event()
        self.record_button.config(text="Stop")

        def worker():
            data = record(speaker, stop_event=self.stop_event)
            path = save_wav(data)
            self.stop_event = None
            self.after(0, self._on_recording_done, path)

        threading.Thread(target=worker, daemon=True).start()

    def _on_recording_done(self, path):
        self.record_button.config(text="Record")
        self._refresh_file_list()
        self.file_var.set(path.name)
        self._load_selected_file()

    # --- Dateiliste ---
    def _refresh_file_list(self):
        SNIPPET_DIR.mkdir(exist_ok=True)
        files = sorted(p.name for p in SNIPPET_DIR.glob("*.wav"))
        self.file_combo.config(values=files)
        if files and not self.file_var.get():
            self.file_var.set(files[-1])
            self._load_selected_file()

    def _load_selected_file(self):
        name = self.file_var.get()
        if not name:
            return
        self._stop_playback()
        rate, samples = wavfile.read(SNIPPET_DIR / name)
        self.audio = samples.astype(np.float32) / 32768.0
        self.sample_rate = rate
        self.selection = None
        self.play_start = None
        self._update_selection_db()
        self._redraw()

    def _delete_snippet(self):
        name = self.file_var.get()
        if not name:
            return
        self._stop_playback()
        (SNIPPET_DIR / name).unlink()
        self.file_var.set("")
        self._refresh_file_list()
        if not self.file_var.get():
            self.audio = None
            self.sample_rate = None
            self.selection = None
            self.play_start = None
            self._update_selection_db()
            self._redraw()

    def _rename_snippet(self):
        old_name = self.file_var.get()
        if not old_name:
            return
        new_stem = simpledialog.askstring("Rename", "New name:", initialvalue=Path(old_name).stem)
        if not new_stem:
            return
        new_path = SNIPPET_DIR / f"{new_stem}.wav"
        if new_path.exists():
            return
        (SNIPPET_DIR / old_name).rename(new_path)
        self.file_var.set(new_path.name)
        self._refresh_file_list()

    def _redraw(self):
        self.axes.clear()
        self._style_axes()
        self.marker_line = None
        self.playhead_line = None
        # SpanSelector nutzt Blitting (eine gecachte Bildschirm-Kopie statt komplettem
        # Neuzeichnen) - ohne diesen Reset bleibt eine alte Auswahl als visueller "Geist"
        # sichtbar, obwohl self.selection bereits korrekt zurückgesetzt ist.
        self.span_selector.clear()
        if self.audio is not None:
            times = np.arange(len(self.audio)) / self.sample_rate
            self.axes.plot(times, self.audio, linewidth=0.5, color=WAVE_COLOR)
        self.canvas.draw_idle()

    # --- Auswahl (Ziehen) ---
    def _on_select(self, xmin, xmax):
        # Eine neue Auswahl macht eine laufende/pausierte Wiedergabe ungültig - ohne
        # diesen Reset würde ein späteres Play über _resume_playback() an der alten
        # (jetzt falschen) Position weiterspielen statt an der neuen zu starten.
        self._stop_playback()
        # Der SpanSelector kann (je nach Maus-Timing) auch für einen eigentlichen Klick
        # eine winzige Spanne melden statt sie über minspan komplett zu verwerfen - das hier
        # fängt das defensiv ab, damit es nicht zu einer entarteten Nulllängen-Auswahl kommt
        # (die "Play" wirkungslos machen würde), egal ob _on_release oder _on_select zuerst
        # verarbeitet wird.
        if abs(xmax - xmin) < CLICK_EPSILON:
            self.selection = None
            self.play_start = max(0.0, xmin)
            self._draw_marker(self.play_start)
        else:
            self.selection = (xmin, xmax)
            self.play_start = None
        self._update_selection_db()

    # --- Klick-Erkennung (unabhängig vom SpanSelector) ---
    def _on_press(self, event):
        if event.inaxes != self.axes or event.xdata is None:
            self._press_x = None
            return
        self._press_x = event.xdata

    def _on_release(self, event):
        if self._press_x is None or event.inaxes != self.axes or event.xdata is None:
            self._press_x = None
            return
        if abs(event.xdata - self._press_x) < CLICK_EPSILON:
            self._stop_playback()
            self.selection = None
            self.play_start = max(0.0, self._press_x)
            self._draw_marker(self.play_start)
            self._update_selection_db()
        self._press_x = None

    def _draw_marker(self, position):
        if self.marker_line is None:
            self.marker_line = self.axes.axvline(position, color=MARKER_COLOR, linestyle="--", linewidth=1)
        else:
            self.marker_line.set_xdata([position, position])
        self.canvas.draw_idle()

    def _selected_segment(self):
        start, end = self.selection
        start_i = max(0, int(start * self.sample_rate))
        end_i = min(len(self.audio), int(end * self.sample_rate))
        return start_i, end_i, start, end

    def _update_selection_db(self):
        if self.selection is None or self.audio is None:
            self.selection_db_var.set("")
            return
        start_i, end_i, start, end = self._selected_segment()
        segment = self.audio[start_i:end_i]
        if len(segment) == 0:
            self.selection_db_var.set("")
            return
        peak_db = compute_peak_db(segment, self.sample_rate)
        self.selection_db_var.set(f"Selection: {end - start:.2f}s, Peak {peak_db:.1f} dB")

    def _apply_selection_as_threshold(self):
        if self.selection is None or self.audio is None:
            return
        start_i, end_i, _, _ = self._selected_segment()
        segment = self.audio[start_i:end_i]
        if len(segment) == 0:
            return
        peak_db = compute_peak_db(segment, self.sample_rate)
        suggested = round(peak_db - THRESHOLD_SUGGESTION_MARGIN_DB, 1)
        self.threshold_var.set(suggested)
        self._on_monitor_settings_changed()
        self._log(f"Threshold set to {suggested:.1f} dB (peak {peak_db:.1f} dB - {THRESHOLD_SUGGESTION_MARGIN_DB:.0f} dB margin).")

    def _determine_range(self):
        if self.selection:
            start, end = self.selection
            start_i = max(0, int(start * self.sample_rate))
            end_i = min(len(self.audio), int(end * self.sample_rate))
        else:
            offset = self.play_start or 0.0
            start_i = int(offset * self.sample_rate)
            end_i = len(self.audio)
        return start_i, end_i

    # --- Wiedergabe: gemeinsame Hilfsfunktion ---
    def _write_preview_and_play(self, segment, rate):
        pcm16 = (np.clip(segment, -1.0, 1.0) * 32767).astype(np.int16)
        wavfile.write(PREVIEW_PATH, rate, pcm16)
        winsound.PlaySound(str(PREVIEW_PATH), winsound.SND_FILENAME | winsound.SND_ASYNC)

    # --- Play/Pause/Stop für die geladene Aufnahme ---
    def _toggle_play_pause(self):
        if self.audio is None:
            return
        if self.is_playing:
            self._pause_playback()
        elif self.is_paused:
            self._resume_playback()
        else:
            start_i, end_i = self._determine_range()
            if end_i <= start_i:
                return
            self.active_range = (start_i, end_i)
            self._start_playback(start_i)

    def _start_playback(self, start_i):
        end_i = self.active_range[1]
        segment = self.audio[start_i:end_i]
        self._write_preview_and_play(segment, self.sample_rate)

        self.playback_offset = start_i / self.sample_rate
        self.playback_duration = len(segment) / self.sample_rate
        self.playback_started_at = time.perf_counter()
        self.is_playing = True
        self.is_paused = False
        self.play_button.config(text="Pause")
        self._update_playhead()

    def _pause_playback(self):
        winsound.PlaySound(None, winsound.SND_PURGE)
        elapsed = time.perf_counter() - self.playback_started_at
        self.paused_at = min(self.playback_offset + elapsed, self.active_range[1] / self.sample_rate)
        self.is_playing = False
        self.is_paused = True
        if self.playhead_job is not None:
            self.after_cancel(self.playhead_job)
            self.playhead_job = None
        self.play_button.config(text="Play")

    def _resume_playback(self):
        start_i = int(self.paused_at * self.sample_rate)
        if start_i >= self.active_range[1]:
            self._stop_playback()
            return
        self._start_playback(start_i)

    def _stop_playback(self):
        winsound.PlaySound(None, winsound.SND_PURGE)
        if self.playhead_job is not None:
            self.after_cancel(self.playhead_job)
            self.playhead_job = None
        if self.playhead_line is not None:
            self.playhead_line.remove()
            self.playhead_line = None
            self.canvas.draw_idle()
        self.is_playing = False
        self.is_paused = False
        self.active_range = None
        self.paused_at = None
        self.play_button.config(text="Play")

    def _update_playhead(self):
        if not self.is_playing:
            return
        elapsed = time.perf_counter() - self.playback_started_at
        if elapsed >= self.playback_duration:
            self._stop_playback()
            return
        position = self.playback_offset + elapsed
        if self.playhead_line is None:
            self.playhead_line = self.axes.axvline(position, color=PLAYHEAD_COLOR, linewidth=1)
        else:
            self.playhead_line.set_xdata([position, position])
        self.canvas.draw_idle()
        self.playhead_job = self.after(PLAYHEAD_INTERVAL_MS, self._update_playhead)

    # --- Einstellungen (Geraet, Input Manager, Automation bleiben zwischen Starts erhalten) ---
    def _load_settings(self):
        settings = load_settings()

        device_name = settings.get("device")
        if device_name and device_name in self.device_combo["values"]:
            self.device_combo.set(device_name)

        if "threshold" in settings:
            self.threshold_var.set(float(settings["threshold"]))
        self.mod_ctrl_var.set(bool(settings.get("mod_ctrl", False)))
        self.mod_alt_var.set(bool(settings.get("mod_alt", False)))
        self.mod_shift_var.set(bool(settings.get("mod_shift", False)))
        main_key = settings.get("main_key")
        if main_key in keys.MAIN_KEYS:
            self.main_key_var.set(main_key)
        if "angel_timeout_seconds" in settings:
            self.angel_timeout_var.set(float(settings["angel_timeout_seconds"]))

        self.lure_mod_ctrl_var.set(bool(settings.get("lure_mod_ctrl", False)))
        self.lure_mod_alt_var.set(bool(settings.get("lure_mod_alt", False)))
        self.lure_mod_shift_var.set(bool(settings.get("lure_mod_shift", False)))
        lure_main_key = settings.get("lure_main_key")
        if lure_main_key in keys.MAIN_KEYS:
            self.lure_main_key_var.set(lure_main_key)
        if "lure_interval_minutes" in settings:
            self.lure_interval_var.set(float(settings["lure_interval_minutes"]))

        self.attack_mod_ctrl_var.set(bool(settings.get("attack_mod_ctrl", False)))
        self.attack_mod_alt_var.set(bool(settings.get("attack_mod_alt", False)))
        self.attack_mod_shift_var.set(bool(settings.get("attack_mod_shift", False)))
        attack_main_key = settings.get("attack_main_key")
        if attack_main_key in keys.MAIN_KEYS:
            self.attack_main_key_var.set(attack_main_key)
        self.attack_enabled_var.set(bool(settings.get("attack_enabled", False)))

        self.trigger_count = int(settings.get("trigger_count", 0))
        self.total_runtime_seconds = float(settings.get("total_runtime_seconds", 0.0))
        self.trigger_count_var.set(f"Triggers: {self.trigger_count}")
        self._set_runtime_var(self.total_runtime_seconds)

    def _save_settings(self):
        save_settings({
            "device": self.device_combo.get(),
            "threshold": self.threshold_var.get(),
            "mod_ctrl": self.mod_ctrl_var.get(),
            "mod_alt": self.mod_alt_var.get(),
            "mod_shift": self.mod_shift_var.get(),
            "main_key": self.main_key_var.get(),
            "angel_timeout_seconds": self.angel_timeout_var.get(),
            "lure_mod_ctrl": self.lure_mod_ctrl_var.get(),
            "lure_mod_alt": self.lure_mod_alt_var.get(),
            "lure_mod_shift": self.lure_mod_shift_var.get(),
            "lure_main_key": self.lure_main_key_var.get(),
            "lure_interval_minutes": self.lure_interval_var.get(),
            "attack_mod_ctrl": self.attack_mod_ctrl_var.get(),
            "attack_mod_alt": self.attack_mod_alt_var.get(),
            "attack_mod_shift": self.attack_mod_shift_var.get(),
            "attack_main_key": self.attack_main_key_var.get(),
            "attack_enabled": self.attack_enabled_var.get(),
            "trigger_count": self.trigger_count,
            "total_runtime_seconds": self.total_runtime_seconds,
        })

    # --- HID (Human Interface Device) ---
    def _test_input_manager(self):
        try:
            self.input_controller.send_combo("WIN", [])
            self._log("Test: sent 'Win'.")
        except Exception as exc:
            self._log(f"Test failed: {exc}")

    # --- Interception-Treiber (Human Interface Device) ---
    def _refresh_hid_status(self):
        installed = interception_driver.is_installed()
        self.hid_status_var.set(f"HID Driver: {'Installed' if installed else 'Not installed'}")
        self.hid_driver_button.config(text="Uninstall" if installed else "Install")

    def _toggle_hid_driver(self):
        installed = interception_driver.is_installed()
        action = interception_driver.uninstall if installed else interception_driver.install
        verb = "Uninstalling" if installed else "Installing"
        self._log(f"{verb} Interception driver (Windows admin prompt may appear)...")
        self.hid_driver_button.config(state="disabled")

        def worker():
            try:
                action()
                message = "Driver uninstalled." if installed else "Driver installed."
                message += " A restart is required for this to take effect."
            except Exception as exc:
                message = f"Driver {'uninstall' if installed else 'install'} failed: {exc}"
            self.after(0, self._on_hid_driver_done, message)

        threading.Thread(target=worker, daemon=True).start()

    def _on_hid_driver_done(self, message):
        self._log(message)
        self.hid_driver_button.config(state="normal")
        self._refresh_hid_status()

    # --- VB-CABLE (virtuelles Audiogerät) ---
    def _refresh_vbcable_status(self):
        installed = vbcable_driver.is_installed()
        self.vbcable_status_var.set("Installed" if installed else "Not installed")

    def _open_vbcable_download_page(self):
        vbcable_driver.open_download_page()
        self._log("Opened VB-CABLE download page in browser.")

    # --- Live-Erkennung ---
    def _toggle_monitoring(self):
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor = None
            self.monitor_button.config(text="Start")
            self._log("Stopped.")
            # Uhr zuruecksetzen, damit ein spaeterer Start nicht sofort (mit einem laengst
            # veralteten Zeitstempel) einen Timeout ausloest, bevor ueberhaupt ein neuer Biss
            # erkannt wurde.
            self.last_cast_at = None
            self.lure_last_used_at = None
            self.is_attacking = False
            # Laufzeit dieses Start-Stop-Abschnitts in die Gesamtsumme einrechnen und
            # persistieren - die Anzeige zaehlt ab jetzt nicht mehr weiter (siehe
            # _update_runtime_display).
            self.total_runtime_seconds += time.perf_counter() - self.session_started_at
            self.session_started_at = None
            self._set_runtime_var(self.total_runtime_seconds)
            self._save_settings()
            return

        index = self.device_combo.current()
        speaker = resolve_speaker(index if index >= 0 else None)
        threshold = float(self.threshold_var.get())

        self.monitor = LiveMonitor(speaker, threshold, COOLDOWN_SECONDS, self._handle_trigger)
        threading.Thread(target=self.monitor.run, daemon=True).start()
        self.monitor_button.config(text="Stop")
        self._log(f"Started (threshold={threshold:.1f} dB).")

        # Es wird nicht auf den ersten echten Biss gewartet - last_cast_at wird so vorbelegt,
        # dass die Timeout-Routine (siehe _check_angel_trigger_timeout) bereits nach
        # ANGEL_TRIGGER_INITIAL_DELAY_SECONDS erstmals feuert, nicht erst nach dem vollen,
        # in der Oberfläche eingestellten Timeout.
        angel_timeout = float(self.angel_timeout_var.get())
        self.last_cast_at = time.perf_counter() - (angel_timeout - ANGEL_TRIGGER_INITIAL_DELAY_SECONDS)
        self._check_angel_trigger_timeout()

        self.lure_last_used_at = time.perf_counter()
        self._check_lure_timer()

        self.session_started_at = time.perf_counter()
        self._update_runtime_display()

    def _set_runtime_var(self, total_seconds):
        hours, remainder = divmod(int(total_seconds), 3600)
        minutes = remainder // 60
        self.runtime_var.set(f"Runtime: {hours}h {minutes:02d}m")

    def _update_runtime_display(self):
        if self.monitor is None:
            return
        elapsed = self.total_runtime_seconds + (time.perf_counter() - self.session_started_at)
        self._set_runtime_var(elapsed)
        self.after(1000, self._update_runtime_display)

    def _reset_counters(self):
        self.trigger_count = 0
        self.total_runtime_seconds = 0.0
        if self.monitor is not None:
            self.session_started_at = time.perf_counter()
        self.trigger_count_var.set("Triggers: 0")
        self._set_runtime_var(0.0)
        self._save_settings()
        self._log("Trigger counter and runtime reset.")

    def _on_close(self):
        if self.monitor is not None:
            self.total_runtime_seconds += time.perf_counter() - self.session_started_at
            self._save_settings()
        self.destroy()

    def _on_monitor_settings_changed(self):
        # Threshold wird von LiveMonitor bei jedem Block frisch gelesen - laufende Erkennung
        # muss dafür nicht neu gestartet werden.
        self._save_settings()
        if self.monitor is not None:
            self.monitor.threshold_db = float(self.threshold_var.get())

    def _on_device_change(self):
        self._save_settings()
        if self.monitor is not None:
            index = self.device_combo.current()
            speaker = resolve_speaker(index if index >= 0 else None)
            self.monitor.set_speaker(speaker)
            self._log("Audio device changed while running.")

    def _handle_trigger(self, db):
        self.after(0, self._on_trigger_fired, db)

    def _on_trigger_fired(self, db):
        self._log(f"Signal triggered ({db:.1f} dB)")
        # Uhr sofort zuruecksetzen (nicht erst am Ende von _run_angel_trigger, das dauert bis
        # zu ~3.5s) - sonst koennte _check_angel_trigger_timeout in der Zwischenzeit mit dem
        # noch alten Zeitstempel erneut (faelschlich) ausloesen.
        self.last_cast_at = time.perf_counter()
        if self.is_attacking:
            self.is_attacking = False
            self._log("Threshold reached again - stopping attack loop.")
        self.trigger_count += 1
        self.trigger_count_var.set(f"Triggers: {self.trigger_count}")
        self._save_settings()
        threading.Thread(target=self._run_angel_trigger, daemon=True).start()

    def _run_angel_trigger(self):
        # Läuft in einem eigenen Thread, damit die Wartezeiten (bis zu ~2.5s) nicht die
        # Tkinter-Oberfläche blockieren. Das eigentliche Senden + Loggen wird per self.after
        # auf den Hauptthread zurückgeholt (Tkinter ist nicht thread-sicher).
        time.sleep(random.uniform(*ANGEL_TRIGGER_FIRST_DELAY_RANGE))
        self.after(0, self._send_angel_signal)
        time.sleep(ANGEL_TRIGGER_FIXED_DELAY)
        time.sleep(random.uniform(*ANGEL_TRIGGER_SECOND_DELAY_RANGE))
        self.after(0, self._send_angel_signal)
        # Neuer Wurf beginnt jetzt - die Timeout-Uhr (siehe _check_angel_trigger_timeout)
        # läuft ab hier wieder von vorne.
        self.last_cast_at = time.perf_counter()

    def _check_angel_trigger_timeout(self):
        # Läuft periodisch auf dem Tk-Hauptthread, solange die Erkennung aktiv ist (plant
        # sich selbst per self.after neu ein - kein separater Start/Stop dafür nötig). Während
        # eines laufenden Angriffs (siehe _run_attack_loop) wird hier nichts geprüft - die
        # Angriffsschleife übernimmt, bis wieder ein echter Biss erkannt wird.
        if self.monitor is None:
            return
        if not self.is_attacking:
            angel_timeout = float(self.angel_timeout_var.get())
            if (
                self.last_cast_at is not None
                and time.perf_counter() - self.last_cast_at >= angel_timeout
            ):
                self.last_cast_at = time.perf_counter()
                # Nur EIN Tastendruck, kein Unterbrechen+Neuauswerfen: die Taste wirkt wie ein
                # Umschalter (fischt gerade -> wird abgebrochen, fischt nicht -> wirft aus).
                # Ein zweiter Druck kurz danach würde einen frisch gestarteten Wurf sofort
                # wieder abbrechen (Taste toggelt zurück in den Ausgangszustand) - dann bleibt
                # dauerhaft nichts ausgeworfen und der Timeout feuert immer wieder
                # ergebnislos. Mit nur einem Druck pro Timeout pendelt sich der Zustand über
                # die nächsten Zyklen von selbst ein.
                self._log(f"No bite within {angel_timeout:.0f}s - pressing signal once.")
                self._send_angel_signal()
                if self.attack_enabled_var.get():
                    # Erst danach: vermutlich durch einen Angriff unterbrochen - wiederholt
                    # angreifen (siehe _run_attack_loop), bis wieder ein echter Biss
                    # (Threshold) erkannt wird (siehe _on_trigger_fired).
                    self._log("Starting attack loop.")
                    self.is_attacking = True
                    self._run_attack_loop()
        self.after(ANGEL_TRIGGER_TIMEOUT_CHECK_MS, self._check_angel_trigger_timeout)

    def _run_attack_loop(self):
        if self.monitor is None or not self.is_attacking:
            return
        self._send_attack_signal()
        self.after(int(ATTACK_TRIGGER_INTERVAL_SECONDS * 1000), self._run_attack_loop)

    def _check_lure_timer(self):
        # Läuft periodisch auf dem Tk-Hauptthread, solange die Erkennung aktiv ist (plant
        # sich selbst per self.after neu ein - kein separater Start/Stop dafür nötig).
        if self.monitor is None:
            return
        interval_minutes = float(self.lure_interval_var.get())
        # 0 Minuten schaltet das Intervall ab - kein automatisches Senden.
        if (
            interval_minutes > 0
            and self.lure_last_used_at is not None
            and time.perf_counter() - self.lure_last_used_at >= interval_minutes * 60.0
        ):
            self._log(f"Lure interval elapsed ({interval_minutes:.0f} min).")
            self._send_lure_signal()
        self.after(LURE_TIMER_CHECK_MS, self._check_lure_timer)

    def _send_signal(self, mod_ctrl_var, mod_alt_var, mod_shift_var, key_var):
        key = key_var.get()
        if not key:
            self._log("No signal configured.")
            return
        modifiers = [
            name for name, var in (
                ("ctrl", mod_ctrl_var),
                ("alt", mod_alt_var),
                ("shift", mod_shift_var),
            ) if var.get()
        ]
        label = "+".join(m.capitalize() for m in modifiers + [key])
        try:
            self.input_controller.send_combo(key, modifiers)
            self._log(f"Sent '{label}'.")
        except Exception as exc:
            self._log(f"Send error: {exc}")

    def _send_angel_signal(self):
        self._send_signal(self.mod_ctrl_var, self.mod_alt_var, self.mod_shift_var, self.main_key_var)

    def _send_attack_signal(self):
        self._send_signal(
            self.attack_mod_ctrl_var, self.attack_mod_alt_var, self.attack_mod_shift_var, self.attack_main_key_var
        )

    def _send_lure_signal(self):
        # Uhr zuruecksetzen, damit das naechste automatische Feuern wieder das volle
        # Intervall ab jetzt abwartet.
        self.lure_last_used_at = time.perf_counter()
        self._send_signal(
            self.lure_mod_ctrl_var, self.lure_mod_alt_var, self.lure_mod_shift_var, self.lure_main_key_var
        )

    def _log(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")


if __name__ == "__main__":
    TriggerEditor().mainloop()
