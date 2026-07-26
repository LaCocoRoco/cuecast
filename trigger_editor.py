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
from matcher import LiveMonitor, compute_db
from record_snippet import SNIPPET_DIR, record, save_wav
from settings import load_settings, save_settings
from windows import list_windows
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

# Klicks (keine echte Auswahl) sind kürzer als das hier, in Sekunden. 0.02s (~2 Pixel bei
# typischer Fensterbreite) war zu knapp bemessen für echtes Maus-Zittern bei einem Klick.
CLICK_EPSILON = 0.08
PLAYHEAD_INTERVAL_MS = 30

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

AUTOMATION_DEFAULT = "Default"
AUTOMATION_WOW = "World of Warcraft"

# World-of-Warcraft-Automation: zweimal auslösen (Fangen + Looten) mit menschlich wirkenden,
# zufälligen Verzögerungen statt exaktem Timing. Werte vorerst fix im Code, sollen später bei
# Bedarf noch verfeinert/konfigurierbar werden.
AUTOMATION_WOW_FIRST_DELAY_RANGE = (0.0, 1.0)
AUTOMATION_WOW_FIXED_DELAY = 0.5
AUTOMATION_WOW_SECOND_DELAY_RANGE = (0.0, 1.0)

# Ein Wurf dauert in WoW maximal ~22s. Wurde 20s lang kein Biss (Trigger) erkannt, brechen wir
# den Wurf selbst ab (Taste erneut drücken) und werfen mit derselben festen Pause wie beim
# Fangen/Auswerfen-Signal neu aus - Sicherheitsnetz gegen "hängengebliebene" Würfe.
AUTOMATION_WOW_TIMEOUT_SECONDS = 20.0
AUTOMATION_WOW_TIMEOUT_CHECK_MS = 1000


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

        self.input_controller = InputController()
        self.input_windows = []

        apply_dark_theme(self)
        self._build_widgets()
        self._load_settings()
        self._refresh_file_list()
        self._refresh_input_windows()
        self._refresh_vbcable_status()

        if self.autostart_var.get():
            self._toggle_monitoring()

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
        # Öffnen des mitgelieferten, unveränderten Installers - kein automatischer Download
        # oder Silent-Install, siehe vbcable_driver.py (Lizenzgrund).
        ttk.Label(controls, text="VB-CABLE:").grid(row=0, column=3, sticky="w", padx=(16, 4), pady=2)
        self.vbcable_status_var = tk.StringVar(value="?")
        ttk.Label(controls, textvariable=self.vbcable_status_var).grid(row=0, column=4, sticky="w", padx=4, pady=2)
        self.vbcable_button = ttk.Button(
            controls, text="Installer", width=BUTTON_WIDTH, command=self._open_vbcable_installer
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

        # Zeigt die (hochpass-gefilterte) Lautstärke der per Maus gezogenen Auswahl in dBFS -
        # gleiche Berechnung wie die Live-Erkennung (siehe matcher.compute_db), damit man an
        # einer echten Aufnahme direkt einen sinnvollen Threshold ablesen kann.
        self.selection_db_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.selection_db_var).pack(side="top", anchor="w", padx=8)

        # Input Manager: Post Message braucht ein Ziel-Fenster, Human Interface Device
        # geht immer an das fokussierte Fenster (siehe input_tester.py).
        input_frame = ttk.Frame(self, height=ROW_HEIGHT)
        input_frame.pack_propagate(False)
        input_frame.pack(side="top", fill="x", padx=8, pady=4)

        ttk.Label(input_frame, text="Input Manager:").pack(side="left")
        self.input_manager_var = tk.StringVar(value=InputController.HID_VARIANT)
        self.input_manager_combo = ttk.Combobox(
            input_frame,
            textvariable=self.input_manager_var,
            values=[InputController.HID_VARIANT, InputController.POST_MESSAGE_VARIANT],
            state="readonly",
            width=20,
        )
        self.input_manager_combo.pack(side="left", padx=4)
        self.input_manager_combo.bind("<<ComboboxSelected>>", lambda event: self._on_input_manager_change())

        # Schneller manueller Test des gerade gewählten Input Managers - sendet die
        # Windows-Taste (leicht sichtbar: öffnet/schließt das Startmenü), unabhängig vom
        # unten konfigurierten Automation-Signal. Referenz gespeichert, damit die Fenster-/
        # Treiber-Zeile unten per before= immer davor eingefügt wird (unabhängig davon, wie
        # oft zwischen den beiden Varianten hin- und hergeschaltet wird).
        self.test_button = ttk.Button(input_frame, text="Test", width=BUTTON_WIDTH, command=self._test_input_manager)
        self.test_button.pack(side="left", padx=(16, 4))

        # Eigener Rahmen, damit die Fensterauswahl bei Human Interface Device komplett
        # ausgeblendet werden kann (dort nicht zutreffend - es geht ans fokussierte Fenster).
        self.input_window_row = ttk.Frame(input_frame)
        self.input_window_row.pack(side="left", fill="x", expand=True, before=self.test_button)

        ttk.Label(self.input_window_row, text="Window:").pack(side="left", padx=(16, 4))
        ttk.Button(
            self.input_window_row, text="Refresh", width=BUTTON_WIDTH, command=self._refresh_input_windows
        ).pack(side="right", padx=4)
        self.input_window_var = tk.StringVar()
        self.input_window_combo = ttk.Combobox(
            self.input_window_row, textvariable=self.input_window_var, state="readonly"
        )
        self.input_window_combo.pack(side="left", fill="x", expand=True, padx=4)
        self.input_window_combo.bind("<<ComboboxSelected>>", lambda event: self._on_input_manager_change())

        # Eigener Rahmen für den Interception-Treiber-Status - nur bei Human Interface
        # Device relevant (spiegelbildlich zur Fensterauswahl bei Post Message).
        self.hid_driver_row = ttk.Frame(input_frame)
        self.hid_driver_row.pack(side="left", fill="x", expand=True, before=self.test_button)

        self.hid_status_var = tk.StringVar(value="Driver: ?")
        ttk.Label(self.hid_driver_row, textvariable=self.hid_status_var).pack(side="left", padx=(16, 4))
        self.hid_driver_button = ttk.Button(
            self.hid_driver_row, text="Install", width=BUTTON_WIDTH, command=self._toggle_hid_driver
        )
        self.hid_driver_button.pack(side="right", padx=4)

        # Automation: Was bei einem Audio-Treffer passiert. Default sendet das konfigurierte
        # Signal über den gewählten Input Manager - World of Warcraft ist als Platzhalter
        # für eine spätere statische Tastensequenz vorgesehen.
        automation_frame = ttk.Frame(self, height=ROW_HEIGHT)
        automation_frame.pack_propagate(False)
        automation_frame.pack(side="top", fill="x", padx=8, pady=(0, 4))

        ttk.Label(automation_frame, text="Automation:").pack(side="left")
        self.automation_var = tk.StringVar(value=AUTOMATION_DEFAULT)
        self.automation_combo = ttk.Combobox(
            automation_frame,
            textvariable=self.automation_var,
            values=[AUTOMATION_DEFAULT, AUTOMATION_WOW],
            state="readonly",
            width=20,
        )
        self.automation_combo.pack(side="left", padx=4)
        self.automation_combo.bind("<<ComboboxSelected>>", lambda event: self._save_settings())

        ttk.Label(automation_frame, text="Signal:").pack(side="left", padx=(16, 0))
        self.mod_ctrl_var = tk.BooleanVar(value=False)
        self.mod_alt_var = tk.BooleanVar(value=False)
        self.mod_shift_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(automation_frame, text="Ctrl", variable=self.mod_ctrl_var, command=self._save_settings).pack(side="left", padx=4)
        ttk.Checkbutton(automation_frame, text="Alt", variable=self.mod_alt_var, command=self._save_settings).pack(side="left", padx=4)
        ttk.Checkbutton(automation_frame, text="Shift", variable=self.mod_shift_var, command=self._save_settings).pack(side="left", padx=4)

        self.main_key_var = tk.StringVar(value="F6")
        self.main_key_combo = ttk.Combobox(
            automation_frame, textvariable=self.main_key_var, values=keys.MAIN_KEYS, state="readonly", width=8
        )
        self.main_key_combo.pack(side="left", padx=4)
        self.main_key_combo.bind("<<ComboboxSelected>>", lambda event: self._save_settings())

        # Live-Erkennung: Threshold/Cooldown/Start + Log
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

        ttk.Label(monitor_frame, text="Cooldown (s):").pack(side="left", padx=(16, 0))
        self.cooldown_var = tk.DoubleVar(value=2.0)
        cooldown_spinbox = ttk.Spinbox(
            monitor_frame, from_=0.5, to=10.0, increment=0.5, textvariable=self.cooldown_var, width=6
        )
        cooldown_spinbox.pack(side="left", padx=4)
        for event in ("<FocusOut>", "<Return>", "<<Increment>>", "<<Decrement>>"):
            cooldown_spinbox.bind(event, lambda e: self._on_monitor_settings_changed())

        self.monitor_button = ttk.Button(monitor_frame, text="Start", width=BUTTON_WIDTH, command=self._toggle_monitoring)
        self.monitor_button.pack(side="left", padx=(16, 4))

        # Startet die Live-Erkennung automatisch, wenn Trigger Editor geöffnet wird (siehe
        # __init__) - nicht zu verwechseln mit einem Windows-Autostart der Anwendung selbst.
        self.autostart_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(monitor_frame, text="Autostart", variable=self.autostart_var, command=self._save_settings).pack(side="left", padx=(8, 4))

        self.log_text = tk.Text(
            self, height=6, bg=BG_PANEL, fg=FG, insertbackground=FG,
            relief="flat", borderwidth=0, state="disabled", wrap="none",
        )
        self.log_text.pack(side="top", fill="x", padx=8, pady=(0, 8))

        self.update_idletasks()
        grid_width = max(
            controls.winfo_reqwidth(),
            input_frame.winfo_reqwidth(),
            automation_frame.winfo_reqwidth(),
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
        db = compute_db(segment, self.sample_rate)
        self.selection_db_var.set(f"Selection: {end - start:.2f}s, {db:.1f} dB")

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

        input_manager = settings.get("input_manager")
        if input_manager in (InputController.POST_MESSAGE_VARIANT, InputController.HID_VARIANT):
            self.input_manager_var.set(input_manager)

        automation = settings.get("automation")
        if automation in (AUTOMATION_DEFAULT, AUTOMATION_WOW):
            self.automation_var.set(automation)

        if "autostart" in settings:
            self.autostart_var.set(bool(settings["autostart"]))
        if "threshold" in settings:
            self.threshold_var.set(float(settings["threshold"]))
        if "cooldown" in settings:
            self.cooldown_var.set(float(settings["cooldown"]))
        self.mod_ctrl_var.set(bool(settings.get("mod_ctrl", False)))
        self.mod_alt_var.set(bool(settings.get("mod_alt", False)))
        self.mod_shift_var.set(bool(settings.get("mod_shift", False)))
        main_key = settings.get("main_key")
        if main_key in keys.MAIN_KEYS:
            self.main_key_var.set(main_key)

    def _save_settings(self):
        save_settings({
            "device": self.device_combo.get(),
            "input_manager": self.input_manager_var.get(),
            "automation": self.automation_var.get(),
            "autostart": self.autostart_var.get(),
            "threshold": self.threshold_var.get(),
            "cooldown": self.cooldown_var.get(),
            "mod_ctrl": self.mod_ctrl_var.get(),
            "mod_alt": self.mod_alt_var.get(),
            "mod_shift": self.mod_shift_var.get(),
            "main_key": self.main_key_var.get(),
        })

    # --- Text Input Manager ---
    def _refresh_input_windows(self):
        self.input_windows = list_windows()
        labels = [f"{w.title} — {w.process_name}" for w in self.input_windows]
        self.input_window_combo.config(values=labels)
        self._on_input_manager_change()

    def _on_input_manager_change(self):
        variant = self.input_manager_var.get()
        is_post_message = variant == InputController.POST_MESSAGE_VARIANT

        if is_post_message:
            self.input_window_combo.config(state="readonly")
            self.input_window_row.pack(side="left", fill="x", expand=True, before=self.test_button)
            self.hid_driver_row.pack_forget()
        else:
            self.input_window_row.pack_forget()
            self.hid_driver_row.pack(side="left", fill="x", expand=True, before=self.test_button)
            self._refresh_hid_status()

        hwnd = None
        if is_post_message:
            index = self.input_window_combo.current()
            if index >= 0:
                hwnd = self.input_windows[index].hwnd
        self.input_controller.set_variant(variant, hwnd)
        self._save_settings()

    def _test_input_manager(self):
        try:
            self.input_controller.send_combo("WIN", [])
            self._log(f"Test: sent 'Win' via {self.input_manager_var.get()}.")
        except Exception as exc:
            self._log(f"Test failed: {exc}")

    # --- Interception-Treiber (Human Interface Device) ---
    def _refresh_hid_status(self):
        installed = interception_driver.is_installed()
        self.hid_status_var.set(f"Driver: {'Installed' if installed else 'Not installed'}")
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

    def _open_vbcable_installer(self):
        self._log("Opening VB-CABLE installer (Windows admin prompt may appear)...")
        self.vbcable_button.config(state="disabled")

        def worker():
            try:
                vbcable_driver.open_installer()
                message = "VB-CABLE installer closed."
            except Exception as exc:
                message = f"VB-CABLE installer failed: {exc}"
            self.after(0, self._on_vbcable_installer_done, message)

        threading.Thread(target=worker, daemon=True).start()

    def _on_vbcable_installer_done(self, message):
        self._log(message)
        self.vbcable_button.config(state="normal")
        self._refresh_vbcable_status()

    # --- Live-Erkennung ---
    def _toggle_monitoring(self):
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor = None
            self.monitor_button.config(text="Start")
            self._log("Stopped.")
            return

        index = self.device_combo.current()
        speaker = resolve_speaker(index if index >= 0 else None)
        threshold = float(self.threshold_var.get())
        cooldown = float(self.cooldown_var.get())

        self.monitor = LiveMonitor(speaker, threshold, cooldown, self._handle_trigger)
        threading.Thread(target=self.monitor.run, daemon=True).start()
        self.monitor_button.config(text="Stop")
        self._log(f"Started (threshold={threshold:.1f} dB, cooldown={cooldown:.1f}s).")

        self.last_cast_at = time.perf_counter()
        self._check_wow_timeout()

    def _on_monitor_settings_changed(self):
        # Threshold/Cooldown werden von LiveMonitor bei jedem Block frisch gelesen - laufende
        # Erkennung muss dafür nicht neu gestartet werden.
        self._save_settings()
        if self.monitor is not None:
            self.monitor.threshold_db = float(self.threshold_var.get())
            self.monitor.cooldown = float(self.cooldown_var.get())

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
        automation = self.automation_var.get()
        if automation == AUTOMATION_DEFAULT:
            self._send_default_signal()
        elif automation == AUTOMATION_WOW:
            threading.Thread(target=self._run_wow_automation, daemon=True).start()
        else:
            self._log(f"Automation '{automation}': not yet implemented.")

    def _run_wow_automation(self):
        # Läuft in einem eigenen Thread, damit die Wartezeiten (bis zu ~2.5s) nicht die
        # Tkinter-Oberfläche blockieren. Das eigentliche Senden + Loggen wird per self.after
        # auf den Hauptthread zurückgeholt (Tkinter ist nicht thread-sicher).
        time.sleep(random.uniform(*AUTOMATION_WOW_FIRST_DELAY_RANGE))
        self.after(0, self._send_default_signal)
        time.sleep(AUTOMATION_WOW_FIXED_DELAY)
        time.sleep(random.uniform(*AUTOMATION_WOW_SECOND_DELAY_RANGE))
        self.after(0, self._send_default_signal)
        # Neuer Wurf beginnt jetzt - die Timeout-Uhr (siehe _check_wow_timeout) läuft ab hier
        # wieder von vorne.
        self.last_cast_at = time.perf_counter()

    def _check_wow_timeout(self):
        # Läuft periodisch auf dem Tk-Hauptthread, solange die Erkennung aktiv ist (plant
        # sich selbst per self.after neu ein - kein separater Start/Stop dafür nötig).
        if self.monitor is None:
            return
        if (
            self.automation_var.get() == AUTOMATION_WOW
            and self.last_cast_at is not None
            and time.perf_counter() - self.last_cast_at >= AUTOMATION_WOW_TIMEOUT_SECONDS
        ):
            self._log(f"No bite within {AUTOMATION_WOW_TIMEOUT_SECONDS:.0f}s - interrupting and recasting.")
            # Uhr sofort zurücksetzen, damit der Timeout nicht mehrfach hintereinander feuert,
            # während die Interrupt+Recast-Sequenz im Hintergrund-Thread noch läuft.
            self.last_cast_at = time.perf_counter()
            threading.Thread(target=self._run_wow_timeout_recast, daemon=True).start()
        self.after(AUTOMATION_WOW_TIMEOUT_CHECK_MS, self._check_wow_timeout)

    def _run_wow_timeout_recast(self):
        self.after(0, self._send_default_signal)  # Wurf unterbrechen
        time.sleep(AUTOMATION_WOW_FIXED_DELAY)
        self.after(0, self._send_default_signal)  # erneut auswerfen

    def _send_default_signal(self):
        key = self.main_key_var.get()
        if not key:
            self._log("No signal configured.")
            return
        modifiers = [
            name for name, var in (
                ("ctrl", self.mod_ctrl_var),
                ("alt", self.mod_alt_var),
                ("shift", self.mod_shift_var),
            ) if var.get()
        ]
        label = "+".join(m.capitalize() for m in modifiers + [key])
        try:
            self.input_controller.send_combo(key, modifiers)
            self._log(f"Sent '{label}' via {self.input_manager_var.get()}.")
        except Exception as exc:
            self._log(f"Send error: {exc}")

    def _log(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")


if __name__ == "__main__":
    TriggerEditor().mainloop()
