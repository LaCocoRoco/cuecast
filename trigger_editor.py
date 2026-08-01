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
    bind_tooltip,
)

PREVIEW_PATH = Path(__file__).parent / "_preview.wav"

# Clicks (not a real selection) are shorter than this, in seconds. Used to be 0.08s -
# but that made real, short markings (e.g. short splash transients < 80ms) get wrongly
# treated as a click instead of a selection. 0.03s is a compromise between the two.
CLICK_EPSILON = 0.03
PLAYHEAD_INTERVAL_MS = 30

# Safety margin (fixed dB difference, not a percentage - see compute_peak_db) below the
# measured peak of a marked selection, when it is applied as the Threshold via the button.
THRESHOLD_SUGGESTION_MARGIN_DB = 3.0

# Minimum/default window size (pixels). Must be at least as large as the controls'
# actual space requirement (see winfo_reqwidth/reqheight).
MIN_WIDTH = 700
MIN_HEIGHT = 460

# Uniform widths, so dropdowns/buttons in the same column are the same size.
COMBO_WIDTH = 24
BUTTON_WIDTH = 11

# Uniform row height for all control rows (not terminal/waveform), so nothing shifts up
# and down slightly depending on row content (button vs. entry vs. checkbutton).
ROW_HEIGHT = 33

# Fixed height of the waveform display (pixels) - does not change with window size.
WAVEFORM_HEIGHT = 100

# ============================================================================
# Fishing trigger timing - the actual values are user-adjustable via the "Timing" dialog
# (see _toggle_timing_dialog) and persisted in settings.json; these are just the defaults for
# a fresh settings.json (see __init__ for where the Tk variables are created).
# Fishing/Lure/Utility Delay each have a companion "range" value (also in the Timing
# dialog): 0 keeps the delay fixed at exactly the base value; a nonzero range instead picks
# a random delay somewhere between the base value and the range value (see
# _resolve_randomized_delay) - a simple way to make the timing less uniform/predictable
# without giving up the option of a precise fixed delay. Start Delay is deliberately fixed,
# no range - it's a one-off startup wait, not part of the humanized in-game timing.
# ============================================================================
# Fixed pause between the catch signal and casting again.
FISHING_DELAY_DEFAULT = 1.5
FISHING_DELAY_RANGE_DEFAULT = 2.5
# The timeout itself is configurable via "Timeout (s)" in the UI
# (self.fishing_timeout_var). How often the timeout is checked - not a timing constant of
# the sequence itself, usually doesn't need to be adjusted.
FISHING_TRIGGER_TIMEOUT_CHECK_MS = 1000
# At Start, we don't wait for the first real bite - the routine instead runs for the
# first time already after this short startup delay (see _toggle_monitoring).
START_DELAY_DEFAULT = 5.0

# How often it's checked whether the Attack interval has elapsed (see _check_attack_timer) -
# not a timing constant of the sequence itself, usually doesn't need to be adjusted.
ATTACK_TIMER_CHECK_MS = 1000

# Added on top of the configured Attack Interval (see _check_attack_timer/_send_attack_signal) -
# a fixed interval alone (e.g. exactly every 4s) is not human, no one presses a button on such
# a precise cadence. A fresh value is picked each time Attack fires, for the next cycle.
ATTACK_DELAY_DEFAULT = 1.5
ATTACK_DELAY_RANGE_DEFAULT = 2.5

# Time between two live detection hits.
COOLDOWN_DEFAULT = 2.0

# Its own pause after the Lure signal, independent of the fishing delay - the lure itself
# also takes a moment until it's actually applied/landed.
LURE_DELAY_DEFAULT = 1.5
LURE_DELAY_RANGE_DEFAULT = 2.5

# Its own pause after the Utility signal, same idea as Lure Delay - Utility works exactly
# like Lure (no timer of its own, only used as part of a real bite once its own delay has
# elapsed since last use, see _should_use_utility).
UTILITY_DELAY_DEFAULT = 1.5
UTILITY_DELAY_RANGE_DEFAULT = 2.5

# The lure itself causes a splash sound when it hits the water, which exceeds the
# Threshold again - without this lockout, that would trigger a new, overlapping
# fishing-trigger sequence while the one triggered by the lure is still running (see
# _on_trigger_fired).
LURE_SPLASH_IGNORE_DEFAULT = 4.0

# Spinbox range/step shared by all timing fields in the "Timing" dialog.
TIMING_DIALOG_SPINBOX_RANGE = (0.0, 60.0, 0.5)


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
        self.active_range = None  # (start_sample, end_sample) of the current playback
        self.paused_at = None  # position in seconds when paused
        self.playhead_job = None
        self.playback_started_at = None
        self.playback_offset = 0.0
        self.playback_duration = 0.0

        self.monitor = None
        self.last_cast_at = None
        self.lure_last_used_at = None
        # Only set when the lure was actually sent (unlike lure_last_used_at, which is
        # already pre-set at Start) - serves solely to avoid treating the lure's own splash
        # sound as a new bite (see _on_trigger_fired).
        self.lure_fired_at = None
        self.utility_last_used_at = None
        self.attack_last_used_at = None
        # Randomized on top of the configured Attack Interval - a fresh value picked each
        # time Attack fires (see _send_attack_signal), applied to the next cycle by
        # _check_attack_timer.
        self.attack_extra_delay = 0.0

        # Timing values adjustable via the "Timing" dialog (see _toggle_timing_dialog),
        # persisted in settings.json just like the other trigger settings. Fishing/Lure/
        # Utility/Attack Delay each have a companion "range" var - 0 keeps the delay fixed
        # at the base value, a nonzero range randomizes it (see _resolve_randomized_delay).
        # Start Delay is deliberately fixed, no range var.
        self.fishing_delay_var = tk.DoubleVar(value=FISHING_DELAY_DEFAULT)
        self.fishing_delay_range_var = tk.DoubleVar(value=FISHING_DELAY_RANGE_DEFAULT)
        self.start_delay_var = tk.DoubleVar(value=START_DELAY_DEFAULT)
        self.lure_delay_var = tk.DoubleVar(value=LURE_DELAY_DEFAULT)
        self.lure_delay_range_var = tk.DoubleVar(value=LURE_DELAY_RANGE_DEFAULT)
        self.utility_delay_var = tk.DoubleVar(value=UTILITY_DELAY_DEFAULT)
        self.utility_delay_range_var = tk.DoubleVar(value=UTILITY_DELAY_RANGE_DEFAULT)
        self.attack_delay_var = tk.DoubleVar(value=ATTACK_DELAY_DEFAULT)
        self.attack_delay_range_var = tk.DoubleVar(value=ATTACK_DELAY_RANGE_DEFAULT)
        self.lure_splash_ignore_var = tk.DoubleVar(value=LURE_SPLASH_IGNORE_DEFAULT)
        self.cooldown_var = tk.DoubleVar(value=COOLDOWN_DEFAULT)
        self.timing_dialog = None

        # Trigger counter/runtime: persisted via settings.json (see
        # _load_settings/_save_settings), only reset explicitly via the Reset button.
        self.trigger_count = 0
        self.total_runtime_seconds = 0.0
        self.session_started_at = None  # perf_counter() timestamp of the current start-stop segment

        self.input_controller = InputController()

        apply_dark_theme(self)
        self._build_widgets()
        self._load_settings()
        self._refresh_file_list()
        self._update_snippet_view_visibility()
        self._refresh_hid_status()
        self._refresh_vbcable_status()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_widgets(self):
        # Device/snippets rows as a shared grid, so columns (selection, play, stop, delete,
        # rename, ...) line up across the rows.
        controls = ttk.Frame(self)
        controls.pack(side="top", fill="x", padx=8, pady=8)
        for row in range(2):
            controls.grid_rowconfigure(row, minsize=ROW_HEIGHT)

        # Row 0: recording device + Record/Stop
        ttk.Label(controls, text="Device:").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=2)
        device_names = [s.name for s in self.speakers]
        self.device_combo = ttk.Combobox(controls, values=device_names, state="readonly", width=COMBO_WIDTH)
        if device_names:
            self.device_combo.current(0)
        self.device_combo.grid(row=0, column=1, sticky="w", padx=4, pady=2)
        self.device_combo.bind("<<ComboboxSelected>>", lambda event: self._on_device_change())
        self.record_button = ttk.Button(controls, text="Record", width=BUTTON_WIDTH, command=self._toggle_recording)
        self.record_button.grid(row=0, column=2, sticky="w", padx=4, pady=2)

        # VB-CABLE (virtual audio device for computers without a real sound card): just
        # status + a link to the official download page, so the driver always stays current
        # (see vbcable_driver.py) - no bundled/locally installed installer.
        ttk.Label(controls, text="VB-CABLE:").grid(row=0, column=3, sticky="w", padx=(16, 4), pady=2)
        self.vbcable_status_var = tk.StringVar(value="?")
        ttk.Label(controls, textvariable=self.vbcable_status_var).grid(row=0, column=4, sticky="w", padx=4, pady=2)
        self.vbcable_button = ttk.Button(
            controls, text="Download", width=BUTTON_WIDTH, command=self._open_vbcable_download_page
        )
        self.vbcable_button.grid(row=0, column=5, sticky="w", padx=4, pady=2)

        # Row 1: snippet selection + Play/Stop/Delete/Rename/Save
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

        # Waveform: fixed size (width = grid width, height = WAVEFORM_HEIGHT), does not
        # grow/shift with the window size. Deliberately placed here (directly below the
        # recording rows) to visually separate it from the general settings below.
        self.figure = Figure(facecolor=BG)
        self.axes = self.figure.add_subplot(111)
        self._style_axes()
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget = canvas_widget
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

        # Button applies the loudest block (same block size as live detection, see
        # matcher.compute_peak_db) of the mouse-dragged selection minus the safety margin
        # directly as the Threshold - only active while something is selected (see
        # _update_selection_db). Info text next to it, to the right of the button.
        selection_row = ttk.Frame(self)
        self.selection_row = selection_row
        selection_row.pack(side="top", fill="x", padx=8)
        self.apply_threshold_button = ttk.Button(
            selection_row, text="Apply as Threshold", command=self._apply_selection_as_threshold, state="disabled"
        )
        self.apply_threshold_button.pack(side="left")
        self.selection_db_var = tk.StringVar(value="")
        ttk.Label(selection_row, textvariable=self.selection_db_var).pack(side="left", padx=(8, 0))

        # HID (Interception driver, always goes to the focused window) - status +
        # install/uninstall + a quick manual test.
        input_frame = ttk.Frame(self, height=ROW_HEIGHT)
        self.input_frame = input_frame
        input_frame.pack_propagate(False)
        input_frame.pack(side="top", fill="x", padx=8, pady=4)

        self.hid_status_var = tk.StringVar(value="HID Driver: ?")
        ttk.Label(input_frame, textvariable=self.hid_status_var).pack(side="left")
        self.hid_driver_button = ttk.Button(
            input_frame, text="Install", width=BUTTON_WIDTH, command=self._toggle_hid_driver
        )
        self.hid_driver_button.pack(side="left", padx=4)

        # Sends the Windows key (easily visible: opens/closes the Start menu) to quickly
        # test HID independently of the signals configured below.
        ttk.Button(input_frame, text="Test", width=BUTTON_WIDTH, command=self._test_input_manager).pack(
            side="left", padx=(16, 4)
        )

        # Fishing (row 0) + Lure (row 1) + Utility (row 2) + Attack (row 3): shared grid, so
        # the checkbuttons/dropdowns of all rows line up exactly on top of each other (like
        # Device/Snippets above).
        trigger_frame = ttk.Frame(self)
        trigger_frame.pack(side="top", fill="x", padx=8, pady=(0, 4))
        for row in range(4):
            trigger_frame.grid_rowconfigure(row, minsize=ROW_HEIGHT)

        # Row 0: Fishing - signal sent when a bite (audio hit) is detected, see
        # _run_fishing_trigger. Timeout (s): see _check_fishing_trigger_timeout.
        ttk.Label(trigger_frame, text="Fishing:").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=2)
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
        self.fishing_timeout_var = tk.DoubleVar(value=24.0)
        fishing_timeout_spinbox = ttk.Spinbox(
            trigger_frame, from_=5.0, to=60.0, increment=1.0, textvariable=self.fishing_timeout_var, width=6
        )
        fishing_timeout_spinbox.grid(row=0, column=6, sticky="w", padx=4, pady=2)
        for event in ("<FocusOut>", "<Return>", "<<Increment>>", "<<Decrement>>"):
            fishing_timeout_spinbox.bind(event, lambda e: self._save_settings())

        fishing_info_label = ttk.Label(trigger_frame, text="ⓘ", cursor="hand2")
        fishing_info_label.grid(row=0, column=7, sticky="w", padx=(8, 0), pady=2)
        bind_tooltip(fishing_info_label, "Install the 'Better Fishing' addon.")

        # Row 1: Lure - same layout as Fishing, but its own signal to refresh the lure. No
        # timer of its own: only used when a real bite was detected AND at least this
        # interval (seconds) has passed since the last use - then between the catch and cast
        # signal (see _run_fishing_trigger). 0 disables it.
        ttk.Label(trigger_frame, text="Lure:").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=2)
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

        ttk.Label(trigger_frame, text="Interval (s):").grid(row=1, column=5, sticky="w", padx=(16, 4), pady=2)
        self.lure_interval_var = tk.DoubleVar(value=0.0)
        lure_interval_spinbox = ttk.Spinbox(
            trigger_frame, from_=0.0, to=3600.0, increment=10.0, textvariable=self.lure_interval_var, width=6
        )
        lure_interval_spinbox.grid(row=1, column=6, sticky="w", padx=4, pady=2)
        for event in ("<FocusOut>", "<Return>", "<<Increment>>", "<<Decrement>>"):
            lure_interval_spinbox.bind(event, lambda e: self._save_settings())

        # Row 2: Utility - works exactly like Lure (no timer of its own, only used as part of
        # a real bite once its own delay has elapsed since last use, see
        # _should_use_utility/_run_fishing_trigger). 0 disables it.
        ttk.Label(trigger_frame, text="Utility:").grid(row=2, column=0, sticky="w", padx=(0, 4), pady=2)
        self.utility_mod_ctrl_var = tk.BooleanVar(value=False)
        self.utility_mod_alt_var = tk.BooleanVar(value=False)
        self.utility_mod_shift_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(trigger_frame, text="Ctrl", variable=self.utility_mod_ctrl_var, command=self._save_settings).grid(row=2, column=1, sticky="w", padx=4, pady=2)
        ttk.Checkbutton(trigger_frame, text="Alt", variable=self.utility_mod_alt_var, command=self._save_settings).grid(row=2, column=2, sticky="w", padx=4, pady=2)
        ttk.Checkbutton(trigger_frame, text="Shift", variable=self.utility_mod_shift_var, command=self._save_settings).grid(row=2, column=3, sticky="w", padx=4, pady=2)

        self.utility_main_key_var = tk.StringVar(value="F9")
        self.utility_main_key_combo = ttk.Combobox(
            trigger_frame, textvariable=self.utility_main_key_var, values=keys.MAIN_KEYS, state="readonly", width=8
        )
        self.utility_main_key_combo.grid(row=2, column=4, sticky="w", padx=4, pady=2)
        self.utility_main_key_combo.bind("<<ComboboxSelected>>", lambda event: self._save_settings())

        ttk.Label(trigger_frame, text="Interval (s):").grid(row=2, column=5, sticky="w", padx=(16, 4), pady=2)
        self.utility_interval_var = tk.DoubleVar(value=0.0)
        utility_interval_spinbox = ttk.Spinbox(
            trigger_frame, from_=0.0, to=3600.0, increment=10.0, textvariable=self.utility_interval_var, width=6
        )
        utility_interval_spinbox.grid(row=2, column=6, sticky="w", padx=4, pady=2)
        for event in ("<FocusOut>", "<Return>", "<<Increment>>", "<<Decrement>>"):
            utility_interval_spinbox.bind(event, lambda e: self._save_settings())

        # Row 3: Attack - same layout as Fishing/Lure/Utility, just as simple as Lure: runs
        # completely independently on the configured interval (see _check_attack_timer), as
        # long as detection is active. 0 disables it.
        ttk.Label(trigger_frame, text="Attack:").grid(row=3, column=0, sticky="w", padx=(0, 4), pady=2)
        self.attack_mod_ctrl_var = tk.BooleanVar(value=False)
        self.attack_mod_alt_var = tk.BooleanVar(value=False)
        self.attack_mod_shift_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(trigger_frame, text="Ctrl", variable=self.attack_mod_ctrl_var, command=self._save_settings).grid(row=3, column=1, sticky="w", padx=4, pady=2)
        ttk.Checkbutton(trigger_frame, text="Alt", variable=self.attack_mod_alt_var, command=self._save_settings).grid(row=3, column=2, sticky="w", padx=4, pady=2)
        ttk.Checkbutton(trigger_frame, text="Shift", variable=self.attack_mod_shift_var, command=self._save_settings).grid(row=3, column=3, sticky="w", padx=4, pady=2)

        self.attack_main_key_var = tk.StringVar(value="F8")
        self.attack_main_key_combo = ttk.Combobox(
            trigger_frame, textvariable=self.attack_main_key_var, values=keys.MAIN_KEYS, state="readonly", width=8
        )
        self.attack_main_key_combo.grid(row=3, column=4, sticky="w", padx=4, pady=2)
        self.attack_main_key_combo.bind("<<ComboboxSelected>>", lambda event: self._save_settings())

        ttk.Label(trigger_frame, text="Interval (s):").grid(row=3, column=5, sticky="w", padx=(16, 4), pady=2)
        self.attack_interval_var = tk.DoubleVar(value=0.0)
        attack_interval_spinbox = ttk.Spinbox(
            trigger_frame, from_=0.0, to=60.0, increment=1.0, textvariable=self.attack_interval_var, width=6
        )
        attack_interval_spinbox.grid(row=3, column=6, sticky="w", padx=4, pady=2)

        attack_info_label = ttk.Label(trigger_frame, text="ⓘ", cursor="hand2")
        attack_info_label.grid(row=3, column=7, sticky="w", padx=(8, 0), pady=2)
        bind_tooltip(
            attack_info_label,
            "Gameplay > Combat > Enable Action Targeting\n\n"
            "Enables a targeting system that dynamically targets enemies based on where "
            "you're looking. Bind Attack to an ability that requires a target - "
            "otherwise it will interrupt fishing.",
        )
        for event in ("<FocusOut>", "<Return>", "<<Increment>>", "<<Decrement>>"):
            attack_interval_spinbox.bind(event, lambda e: self._save_settings())

        # Live detection: Threshold/Start + log
        monitor_frame = ttk.Frame(self, height=ROW_HEIGHT)
        monitor_frame.pack_propagate(False)
        monitor_frame.pack(side="top", fill="x", padx=8, pady=(0, 8))

        ttk.Label(monitor_frame, text="Threshold (dB):").pack(side="left")
        self.threshold_var = tk.DoubleVar(value=-26.0)
        threshold_spinbox = ttk.Spinbox(
            monitor_frame, from_=-80.0, to=0.0, increment=1.0, textvariable=self.threshold_var, width=6
        )
        threshold_spinbox.pack(side="left", padx=4)
        for event in ("<FocusOut>", "<Return>", "<<Increment>>", "<<Decrement>>"):
            threshold_spinbox.bind(event, lambda e: self._on_monitor_settings_changed())

        self.monitor_button = ttk.Button(monitor_frame, text="Start", width=BUTTON_WIDTH, command=self._toggle_monitoring)
        self.monitor_button.pack(side="left", padx=(16, 4))

        ttk.Button(monitor_frame, text="Timing", width=BUTTON_WIDTH, command=self._toggle_timing_dialog).pack(
            side="left", padx=(4, 4)
        )

        # Trigger counter + runtime: persisted via settings.json (self.trigger_count/
        # self.total_runtime_seconds are already initialized/loaded in __init__).
        self.trigger_count_var = tk.StringVar(value="Trigger: 0")
        ttk.Label(monitor_frame, textvariable=self.trigger_count_var).pack(side="left", padx=(16, 4))

        # Runtime only counts while detection is active (Start pressed), pauses on Stop -
        # see _update_runtime_display/_toggle_monitoring.
        self.runtime_var = tk.StringVar(value="Time: 0h 00m")
        ttk.Label(monitor_frame, textvariable=self.runtime_var).pack(side="left", padx=(8, 4))

        ttk.Button(monitor_frame, text="Reset", width=BUTTON_WIDTH, command=self._reset_counters).pack(
            side="left", padx=(8, 4)
        )

        self.log_text = tk.Text(
            self, height=6, bg=BG_PANEL, fg=FG, insertbackground=FG,
            relief="flat", borderwidth=0, state="disabled", wrap="none",
        )
        # The only element with expand=True - absorbs extra window height when resizing
        # larger, while all rows above it keep their fixed height.
        self.log_text.pack(side="top", fill="both", expand=True, padx=8, pady=(0, 8))

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

    # --- Recording ---
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

    # --- File list ---
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
        self._update_snippet_view_visibility()

    def _update_snippet_view_visibility(self):
        # Hides the waveform and the "Apply as Threshold" row entirely while no snippet is
        # loaded (none ever recorded yet, or all deleted) - saves screen space that would
        # otherwise sit empty/unused most of the time. Re-packed with before=self.input_frame
        # so they land back in their original spot instead of at the end of the pack order.
        has_snippet = bool(self.file_var.get())
        if has_snippet:
            if not self.canvas_widget.winfo_ismapped():
                self.canvas_widget.pack(side="top", anchor="w", padx=8, pady=8, before=self.input_frame)
            if not self.selection_row.winfo_ismapped():
                self.selection_row.pack(side="top", fill="x", padx=8, before=self.input_frame)
        else:
            self.canvas_widget.pack_forget()
            self.selection_row.pack_forget()

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
            self._update_snippet_view_visibility()

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
        # SpanSelector uses blitting (a cached screen copy instead of a full redraw) -
        # without this reset, an old selection stays visible as a "ghost", even though
        # self.selection has already been reset correctly.
        self.span_selector.clear()
        if self.audio is not None:
            times = np.arange(len(self.audio)) / self.sample_rate
            self.axes.plot(times, self.audio, linewidth=0.5, color=WAVE_COLOR)
        self.canvas.draw_idle()

    # --- Selection (drag) ---
    def _on_select(self, xmin, xmax):
        # A new selection invalidates any running/paused playback - without this reset, a
        # later Play via _resume_playback() would continue at the old (now wrong) position
        # instead of starting at the new one.
        self._stop_playback()
        # Depending on mouse timing, the SpanSelector can report a tiny span for an actual
        # click instead of discarding it entirely via minspan - this defensively catches
        # that, so it doesn't result in a degenerate zero-length selection (which would make
        # "Play" ineffective), regardless of whether _on_release or _on_select is processed
        # first.
        if abs(xmax - xmin) < CLICK_EPSILON:
            self.selection = None
            self.play_start = max(0.0, xmin)
            self._draw_marker(self.play_start)
        else:
            self.selection = (xmin, xmax)
            self.play_start = None
        self._update_selection_db()

    # --- Click detection (independent of the SpanSelector) ---
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
            self.apply_threshold_button.config(state="disabled")
            return
        start_i, end_i, start, end = self._selected_segment()
        segment = self.audio[start_i:end_i]
        if len(segment) == 0:
            self.selection_db_var.set("")
            self.apply_threshold_button.config(state="disabled")
            return
        peak_db = compute_peak_db(segment, self.sample_rate)
        self.selection_db_var.set(f"Selection: {end - start:.2f}s, Peak {peak_db:.1f} dB")
        self.apply_threshold_button.config(state="normal")

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

    # --- Playback: shared helper function ---
    def _write_preview_and_play(self, segment, rate):
        pcm16 = (np.clip(segment, -1.0, 1.0) * 32767).astype(np.int16)
        wavfile.write(PREVIEW_PATH, rate, pcm16)
        winsound.PlaySound(str(PREVIEW_PATH), winsound.SND_FILENAME | winsound.SND_ASYNC)

    # --- Play/Pause/Stop for the loaded recording ---
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

    # --- Settings (device, input manager, automation persist across restarts) ---
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
        if "fishing_timeout_seconds" in settings:
            self.fishing_timeout_var.set(float(settings["fishing_timeout_seconds"]))

        self.lure_mod_ctrl_var.set(bool(settings.get("lure_mod_ctrl", False)))
        self.lure_mod_alt_var.set(bool(settings.get("lure_mod_alt", False)))
        self.lure_mod_shift_var.set(bool(settings.get("lure_mod_shift", False)))
        lure_main_key = settings.get("lure_main_key")
        if lure_main_key in keys.MAIN_KEYS:
            self.lure_main_key_var.set(lure_main_key)
        if "lure_interval_seconds" in settings:
            self.lure_interval_var.set(float(settings["lure_interval_seconds"]))

        self.utility_mod_ctrl_var.set(bool(settings.get("utility_mod_ctrl", False)))
        self.utility_mod_alt_var.set(bool(settings.get("utility_mod_alt", False)))
        self.utility_mod_shift_var.set(bool(settings.get("utility_mod_shift", False)))
        utility_main_key = settings.get("utility_main_key")
        if utility_main_key in keys.MAIN_KEYS:
            self.utility_main_key_var.set(utility_main_key)
        if "utility_interval_seconds" in settings:
            self.utility_interval_var.set(float(settings["utility_interval_seconds"]))

        self.attack_mod_ctrl_var.set(bool(settings.get("attack_mod_ctrl", False)))
        self.attack_mod_alt_var.set(bool(settings.get("attack_mod_alt", False)))
        self.attack_mod_shift_var.set(bool(settings.get("attack_mod_shift", False)))
        attack_main_key = settings.get("attack_main_key")
        if attack_main_key in keys.MAIN_KEYS:
            self.attack_main_key_var.set(attack_main_key)
        if "attack_interval_seconds" in settings:
            self.attack_interval_var.set(float(settings["attack_interval_seconds"]))

        if "fishing_delay_seconds" in settings:
            self.fishing_delay_var.set(float(settings["fishing_delay_seconds"]))
        if "fishing_delay_range_seconds" in settings:
            self.fishing_delay_range_var.set(float(settings["fishing_delay_range_seconds"]))
        if "start_delay_seconds" in settings:
            self.start_delay_var.set(float(settings["start_delay_seconds"]))
        if "lure_delay_seconds" in settings:
            self.lure_delay_var.set(float(settings["lure_delay_seconds"]))
        if "lure_delay_range_seconds" in settings:
            self.lure_delay_range_var.set(float(settings["lure_delay_range_seconds"]))
        if "utility_delay_seconds" in settings:
            self.utility_delay_var.set(float(settings["utility_delay_seconds"]))
        if "utility_delay_range_seconds" in settings:
            self.utility_delay_range_var.set(float(settings["utility_delay_range_seconds"]))
        if "attack_delay_seconds" in settings:
            self.attack_delay_var.set(float(settings["attack_delay_seconds"]))
        if "attack_delay_range_seconds" in settings:
            self.attack_delay_range_var.set(float(settings["attack_delay_range_seconds"]))
        if "lure_splash_ignore_seconds" in settings:
            self.lure_splash_ignore_var.set(float(settings["lure_splash_ignore_seconds"]))
        if "cooldown_seconds" in settings:
            self.cooldown_var.set(float(settings["cooldown_seconds"]))

        self.trigger_count = int(settings.get("trigger_count", 0))
        self.total_runtime_seconds = float(settings.get("total_runtime_seconds", 0.0))
        self.trigger_count_var.set(f"Trigger: {self.trigger_count}")
        self._set_runtime_var(self.total_runtime_seconds)

    @staticmethod
    def _safe_float(var):
        # Reads a spinbox-bound DoubleVar without crashing with a TclError if the text field
        # is currently empty/invalid (e.g. while the user is typing in it) - returns None in
        # that case, the caller then simply leaves the value unchanged/skips it.
        try:
            return float(var.get())
        except (ValueError, tk.TclError):
            return None

    def _resolve_randomized_delay(self, base_var, base_default, range_var):
        # base_var/range_var (both read here, on the main thread only) form the Fishing/
        # Start/Lure Delay + range pair from the "Timing" dialog. A range of 0 (the default)
        # keeps the delay fixed at exactly the base value, unchanged from before this
        # feature existed. A nonzero range instead picks a random delay somewhere between
        # the base value and the range value - whichever of the two is smaller/larger
        # doesn't matter, it's sorted below.
        base = self._safe_float(base_var)
        if base is None:
            base = base_default
        range_value = self._safe_float(range_var)
        if not range_value:
            return base
        low, high = sorted((base, range_value))
        return random.uniform(low, high)

    def _save_settings(self):
        settings = {
            "device": self.device_combo.get(),
            "mod_ctrl": self.mod_ctrl_var.get(),
            "mod_alt": self.mod_alt_var.get(),
            "mod_shift": self.mod_shift_var.get(),
            "main_key": self.main_key_var.get(),
            "lure_mod_ctrl": self.lure_mod_ctrl_var.get(),
            "lure_mod_alt": self.lure_mod_alt_var.get(),
            "lure_mod_shift": self.lure_mod_shift_var.get(),
            "lure_main_key": self.lure_main_key_var.get(),
            "utility_mod_ctrl": self.utility_mod_ctrl_var.get(),
            "utility_mod_alt": self.utility_mod_alt_var.get(),
            "utility_mod_shift": self.utility_mod_shift_var.get(),
            "utility_main_key": self.utility_main_key_var.get(),
            "attack_mod_ctrl": self.attack_mod_ctrl_var.get(),
            "attack_mod_alt": self.attack_mod_alt_var.get(),
            "attack_mod_shift": self.attack_mod_shift_var.get(),
            "attack_main_key": self.attack_main_key_var.get(),
            "trigger_count": self.trigger_count,
            "total_runtime_seconds": self.total_runtime_seconds,
        }
        for key, var in (
            ("threshold", self.threshold_var),
            ("fishing_timeout_seconds", self.fishing_timeout_var),
            ("lure_interval_seconds", self.lure_interval_var),
            ("utility_interval_seconds", self.utility_interval_var),
            ("attack_interval_seconds", self.attack_interval_var),
            ("fishing_delay_seconds", self.fishing_delay_var),
            ("fishing_delay_range_seconds", self.fishing_delay_range_var),
            ("start_delay_seconds", self.start_delay_var),
            ("lure_delay_seconds", self.lure_delay_var),
            ("lure_delay_range_seconds", self.lure_delay_range_var),
            ("utility_delay_seconds", self.utility_delay_var),
            ("utility_delay_range_seconds", self.utility_delay_range_var),
            ("attack_delay_seconds", self.attack_delay_var),
            ("attack_delay_range_seconds", self.attack_delay_range_var),
            ("lure_splash_ignore_seconds", self.lure_splash_ignore_var),
            ("cooldown_seconds", self.cooldown_var),
        ):
            value = self._safe_float(var)
            if value is not None:
                settings[key] = value
        save_settings(settings)

    # --- HID (Human Interface Device) ---
    def _test_input_manager(self):
        try:
            self.input_controller.send_combo("WIN", [])
            self._log("Test: sent 'Win'.")
        except Exception as exc:
            self._log(f"Test failed: {exc}")

    # --- Interception driver (Human Interface Device) ---
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

    # --- VB-CABLE (virtual audio device) ---
    def _refresh_vbcable_status(self):
        installed = vbcable_driver.is_installed()
        self.vbcable_status_var.set("Installed" if installed else "Not installed")

    def _open_vbcable_download_page(self):
        vbcable_driver.open_download_page()
        self._log("Opened VB-CABLE download page in browser.")

    # --- Live detection ---
    def _toggle_monitoring(self):
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor = None
            self.monitor_button.config(text="Start")
            self._log("Stopped.")
            # Reset the clock, so a later Start doesn't immediately trigger a timeout (with
            # a long-stale timestamp) before a new bite has even been detected.
            self.last_cast_at = None
            self.lure_last_used_at = None
            self.lure_fired_at = None
            self.utility_last_used_at = None
            self.attack_last_used_at = None
            # Add this start-stop segment's runtime to the total and persist it - the
            # display stops counting up from here (see _update_runtime_display).
            self.total_runtime_seconds += time.perf_counter() - self.session_started_at
            self.session_started_at = None
            self._set_runtime_var(self.total_runtime_seconds)
            self._save_settings()
            return

        index = self.device_combo.current()
        speaker = resolve_speaker(index if index >= 0 else None)
        # Falls back to the default value if the field is currently (e.g. mid-typing)
        # empty/invalid - should not prevent "Start".
        threshold = self._safe_float(self.threshold_var)
        if threshold is None:
            threshold = -26.0

        cooldown = self._safe_float(self.cooldown_var)
        if cooldown is None:
            cooldown = COOLDOWN_DEFAULT
        self.monitor = LiveMonitor(speaker, threshold, cooldown, self._handle_trigger)
        threading.Thread(target=self.monitor.run, daemon=True).start()
        self.monitor_button.config(text="Stop")
        self._log(f"Started (threshold={threshold:.1f} dB).")

        # Deliberately left unset (stays None from __init__/Stop) - _check_fishing_trigger_timeout
        # only checks the timeout at all once last_cast_at is no longer None, so it stays a
        # harmless no-op until _run_startup_sequence below sets it for real, once the first
        # actual cast happens. Setting it here already (before Lure/Utility have even run)
        # would start the timeout clock too early - it could then elapse while Lure/Utility
        # are still in progress.
        self._check_fishing_trigger_timeout()

        # No more periodic timer of its own - the lure is now only used as part of a real
        # bite (see _on_trigger_fired/_run_fishing_trigger), lure_last_used_at there only
        # serves as the reference timestamp for "has the wait time elapsed". Utility works
        # exactly the same way. Pre-set here as a default in case either is disabled
        # (interval 0) - if enabled, _run_startup_sequence below updates it to the actual
        # time it was first used instead.
        self.lure_last_used_at = time.perf_counter()
        self.utility_last_used_at = time.perf_counter()

        # We don't wait for the first real bite: right after the Start Delay, run once
        # through (if enabled) Lure -> (if enabled) Utility -> Fishing (the actual first
        # cast, which is what starts the timeout clock) - the same idea as a real bite
        # (_run_fishing_trigger: reel in -> Lure -> Utility -> cast again), just without a
        # "reel in" step since nothing was cast yet, triggered by Start instead of a
        # detection. Read here (main thread) and passed into the background thread as plain
        # values, since Tk variables must not be read from a background thread.
        start_delay = self._safe_float(self.start_delay_var)
        if start_delay is None:
            start_delay = START_DELAY_DEFAULT
        lure_interval = self._safe_float(self.lure_interval_var)
        lure_enabled = lure_interval is not None and lure_interval > 0
        lure_delay = self._resolve_randomized_delay(
            self.lure_delay_var, LURE_DELAY_DEFAULT, self.lure_delay_range_var
        )
        utility_interval = self._safe_float(self.utility_interval_var)
        utility_enabled = utility_interval is not None and utility_interval > 0
        utility_delay = self._resolve_randomized_delay(
            self.utility_delay_var, UTILITY_DELAY_DEFAULT, self.utility_delay_range_var
        )
        threading.Thread(
            target=self._run_startup_sequence,
            args=(start_delay, lure_enabled, lure_delay, utility_enabled, utility_delay),
            daemon=True,
        ).start()

        self.attack_last_used_at = time.perf_counter()
        self.attack_extra_delay = self._resolve_randomized_delay(
            self.attack_delay_var, ATTACK_DELAY_DEFAULT, self.attack_delay_range_var
        )
        self._check_attack_timer()

        self.session_started_at = time.perf_counter()
        self._update_runtime_display()

    def _set_runtime_var(self, total_seconds):
        hours, remainder = divmod(int(total_seconds), 3600)
        minutes = remainder // 60
        self.runtime_var.set(f"Time: {hours}h {minutes:02d}m")

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
        self.trigger_count_var.set("Trigger: 0")
        self._set_runtime_var(0.0)
        self._save_settings()
        self._log("Trigger counter and runtime reset.")

    def _toggle_timing_dialog(self):
        # Advanced timing values that don't have their own field in the main window - kept
        # in a separate dialog so the main window doesn't get cluttered as more of these
        # get added over time. Pressing "Timing" again while it's open just closes it.
        if self.timing_dialog is not None and self.timing_dialog.winfo_exists():
            self.timing_dialog.destroy()
            self.timing_dialog = None
            return

        dialog = tk.Toplevel(self)
        dialog.title("Timing")
        dialog.configure(bg=BG)
        dialog.resizable(False, False)
        dialog.transient(self)
        self.timing_dialog = dialog

        def on_close():
            self.timing_dialog = None
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", on_close)

        frame = ttk.Frame(dialog)
        frame.pack(padx=12, pady=12)

        # Fishing/Lure/Utility/Attack Delay each get a companion "range" field (see
        # _resolve_randomized_delay): 0 keeps the delay fixed at the base value, a nonzero
        # value randomizes it somewhere between the two fields. Attack Delay is added on top
        # of the configured Attack Interval (see _check_attack_timer), unlike the others
        # which are in-between pauses within a sequence. Start Delay is fixed, no range field -
        # it's a one-off startup wait, not part of the humanized in-game timing.
        fields = (
            ("Fishing Delay (s):", self.fishing_delay_var, TIMING_DIALOG_SPINBOX_RANGE, self.fishing_delay_range_var),
            ("Lure Delay (s):", self.lure_delay_var, TIMING_DIALOG_SPINBOX_RANGE, self.lure_delay_range_var),
            ("Utility Delay (s):", self.utility_delay_var, TIMING_DIALOG_SPINBOX_RANGE, self.utility_delay_range_var),
            ("Attack Delay (s):", self.attack_delay_var, TIMING_DIALOG_SPINBOX_RANGE, self.attack_delay_range_var),
            ("Start Delay (s):", self.start_delay_var, TIMING_DIALOG_SPINBOX_RANGE, None),
            ("Lure Splash Ignore (s):", self.lure_splash_ignore_var, TIMING_DIALOG_SPINBOX_RANGE, None),
            ("Cooldown (s):", self.cooldown_var, TIMING_DIALOG_SPINBOX_RANGE, None),
        )
        for row, (label, var, (from_, to, increment), range_var) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
            spinbox = ttk.Spinbox(frame, from_=from_, to=to, increment=increment, textvariable=var, width=6)
            spinbox.grid(row=row, column=1, sticky="w", pady=4)
            for event in ("<FocusOut>", "<Return>", "<<Increment>>", "<<Decrement>>"):
                spinbox.bind(event, lambda e: self._on_timing_settings_changed())

            if range_var is not None:
                range_spinbox = ttk.Spinbox(
                    frame, from_=0.0, to=to, increment=increment, textvariable=range_var, width=6
                )
                range_spinbox.grid(row=row, column=2, sticky="w", padx=(4, 0), pady=4)
                for event in ("<FocusOut>", "<Return>", "<<Increment>>", "<<Decrement>>"):
                    range_spinbox.bind(event, lambda e: self._on_timing_settings_changed())

        info_label = ttk.Label(frame, text="ⓘ", cursor="hand2")
        info_label.grid(row=0, column=3, sticky="w", padx=(8, 0), pady=4)
        bind_tooltip(
            info_label,
            "The second field next to Fishing/Start/Lure Delay is a range: leave it at 0 "
            "for a fixed delay (the first field), or enter a value to pick a random delay "
            "between the two fields each time - which field is larger doesn't matter.",
        )

    def _on_timing_settings_changed(self):
        self._save_settings()
        if self.monitor is not None:
            cooldown = self._safe_float(self.cooldown_var)
            if cooldown is not None:
                self.monitor.cooldown = cooldown

    def _on_close(self):
        if self.monitor is not None:
            self.total_runtime_seconds += time.perf_counter() - self.session_started_at
            self._save_settings()
        self.destroy()

    def _on_monitor_settings_changed(self):
        # Threshold is read fresh by LiveMonitor on every block - running detection doesn't
        # need to be restarted for this.
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
        # The lure itself makes an audible splash when it hits the water and would
        # otherwise immediately re-trigger this trigger, while the sequence it caused is
        # still running (see the "Lure Splash Ignore" timing value) - this was the cause of
        # the overlapping, jumbled-up send order.
        lure_splash_ignore = self._safe_float(self.lure_splash_ignore_var)
        if lure_splash_ignore is None:
            lure_splash_ignore = LURE_SPLASH_IGNORE_DEFAULT
        if (
            self.lure_fired_at is not None
            and time.perf_counter() - self.lure_fired_at < lure_splash_ignore
        ):
            self._log(f"Threshold detected: {db:.1f} dB (ignored, recent lure splash)")
            return

        self._log(f"Threshold detected: {db:.1f} dB")
        # Reset the clock immediately (not only at the end of _run_fishing_trigger, which
        # takes up to ~3.5s) - otherwise _check_fishing_trigger_timeout could incorrectly
        # fire again in the meantime with the still-old timestamp.
        self.last_cast_at = time.perf_counter()
        self.trigger_count += 1
        self.trigger_count_var.set(f"Trigger: {self.trigger_count}")
        self._save_settings()
        # Make these decisions/reads here (main thread), not only in the background thread of
        # _run_fishing_trigger - Tk variables should only be read from the main thread.
        # Deliberately defensive: an invalid/empty spinbox value (e.g. while the user is
        # currently typing in it) must never prevent Fishing itself - at worst,
        # only the lure use is skipped this one time, and the timing values fall back to
        # their coded defaults.
        use_lure = self._should_use_lure()
        fishing_delay = self._resolve_randomized_delay(
            self.fishing_delay_var, FISHING_DELAY_DEFAULT, self.fishing_delay_range_var
        )
        lure_delay = self._resolve_randomized_delay(
            self.lure_delay_var, LURE_DELAY_DEFAULT, self.lure_delay_range_var
        )
        use_utility = self._should_use_utility()
        utility_delay = self._resolve_randomized_delay(
            self.utility_delay_var, UTILITY_DELAY_DEFAULT, self.utility_delay_range_var
        )
        threading.Thread(
            target=self._run_fishing_trigger,
            args=(use_lure, fishing_delay, lure_delay, use_utility, utility_delay),
            daemon=True,
        ).start()

    def _should_use_lure(self):
        try:
            lure_interval = float(self.lure_interval_var.get())
        except (ValueError, tk.TclError):
            return False
        return (
            lure_interval > 0
            and self.lure_last_used_at is not None
            and time.perf_counter() - self.lure_last_used_at >= lure_interval
        )

    def _should_use_utility(self):
        try:
            utility_interval = float(self.utility_interval_var.get())
        except (ValueError, tk.TclError):
            return False
        return (
            utility_interval > 0
            and self.utility_last_used_at is not None
            and time.perf_counter() - self.utility_last_used_at >= utility_interval
        )

    def _run_fishing_trigger(self, use_lure, fishing_delay, lure_delay, use_utility, utility_delay):
        # Runs in its own thread, so the wait times don't block the Tkinter UI. The actual
        # sending + logging is brought back to the main thread via self.after (Tkinter is not
        # thread-safe). Sequence: catch -> Fishing Delay -> (optional) lure as its own
        # in-between sequence with its own Lure Delay afterwards -> (optional) utility as its
        # own in-between sequence with its own Utility Delay afterwards -> cast again. Lure
        # and Utility never overlap - each is fully waited out (signal + its own delay)
        # before the next step starts, even if both happen to be due in the same cycle.
        # fishing_delay/lure_delay/utility_delay are resolved from the "Timing" dialog's Tk
        # variables (base value, or a randomized value within a configured range - see
        # _resolve_randomized_delay) on the main thread by the caller (_on_trigger_fired) and
        # passed in here as plain values, since Tk variables must not be read from a
        # background thread.
        self.after(0, self._send_fishing_signal)
        time.sleep(fishing_delay)
        if use_lure:
            # The lure is used after the catch signal, so it must happen before casting
            # again (see _send_lure_signal, resets lure_last_used_at, which
            # _on_trigger_fired's lure-splash-ignore lockout relies on). Afterwards its own
            # delay (independent of fishing_delay), so the lure itself has time to land
            # before continuing with the normal sequence.
            self.after(0, self._send_lure_signal)
            time.sleep(lure_delay)
        if use_utility:
            # Same idea as the lure: fully sequenced, not overlapping with anything else.
            self.after(0, self._send_utility_signal)
            time.sleep(utility_delay)
        self.after(0, self._send_fishing_signal)
        # A new cast begins now - the timeout clock (see _check_fishing_trigger_timeout)
        # restarts from here.
        self.last_cast_at = time.perf_counter()

    def _run_startup_sequence(self, start_delay, lure_enabled, lure_delay, utility_enabled, utility_delay):
        # Runs once right after Start: (if enabled) Lure, then (if enabled) Utility, fully in
        # sequence (signal + its own delay each) - never overlapping - and only THEN the
        # first actual cast. Otherwise an enabled Lure/Utility would sit unused until its
        # interval AND a real bite happen to coincide, which with a long interval (e.g. 30+
        # minutes) could take a very long time. Mirrors the real-bite sequence in
        # _run_fishing_trigger (reel in -> Lure -> Utility -> cast again), just without a
        # "reel in" step since nothing was cast yet. Waits out the Start Delay first.
        time.sleep(start_delay)
        if lure_enabled:
            self.after(0, self._send_lure_signal)
            time.sleep(lure_delay)
        if utility_enabled:
            self.after(0, self._send_utility_signal)
            time.sleep(utility_delay)
        self.after(0, self._send_fishing_signal)
        # Real timeout counting starts only now, from this first actual cast (not before,
        # while Lure/Utility were still in progress) - so the next "No bite" (see
        # _check_fishing_trigger_timeout) only fires once the full configured timeout has
        # genuinely passed since this cast.
        self.last_cast_at = time.perf_counter()

    def _check_fishing_trigger_timeout(self):
        # Runs periodically on the Tk main thread, as long as detection is active (reschedules
        # itself via self.after - no separate start/stop needed for this). The rescheduling is
        # deliberately in finally: an invalid/empty spinbox value (e.g. while the user is
        # currently typing in it) must never break this chain for the rest of the session -
        # otherwise the timeout would never be checked again.
        if self.monitor is None:
            return
        try:
            fishing_timeout = float(self.fishing_timeout_var.get())
            if (
                self.last_cast_at is not None
                and time.perf_counter() - self.last_cast_at >= fishing_timeout
            ):
                # Only ONE key press, no interrupt+recast: the key acts like a toggle
                # (currently fishing -> gets interrupted, not fishing -> casts). A second
                # press shortly after would immediately interrupt a freshly started cast
                # again (key toggles back to the starting state) - then nothing stays cast
                # and the timeout keeps firing without result. With just one press per
                # timeout, the state settles itself out over the next cycles.
                self._log(f"No bite within {fishing_timeout:.0f}s.")
                self.last_cast_at = time.perf_counter()
                self._send_fishing_signal()
        except (ValueError, tk.TclError) as exc:
            self._log(f"Fishing trigger timeout check error: {exc}")
        finally:
            self.after(FISHING_TRIGGER_TIMEOUT_CHECK_MS, self._check_fishing_trigger_timeout)

    def _check_attack_timer(self):
        # Runs periodically on the Tk main thread, as long as detection is active (reschedules
        # itself via self.after - no separate start/stop needed for this). Deliberately simple
        # and completely independent of Fishing's timeout/threshold - just fires on the
        # configured interval, plus attack_extra_delay (a randomized human touch - see
        # Attack Delay in the "Timing" dialog and _send_attack_signal, which picks a fresh
        # value for the next cycle each time Attack fires). 0 disables it. Rescheduling is
        # deliberately in finally - see _check_fishing_trigger_timeout for the reasoning.
        if self.monitor is None:
            return
        try:
            interval_seconds = float(self.attack_interval_var.get())
            if (
                interval_seconds > 0
                and self.attack_last_used_at is not None
                and time.perf_counter() - self.attack_last_used_at >= interval_seconds + self.attack_extra_delay
            ):
                self._send_attack_signal()
        except (ValueError, tk.TclError) as exc:
            self._log(f"Attack timer check error: {exc}")
        finally:
            self.after(ATTACK_TIMER_CHECK_MS, self._check_attack_timer)

    def _send_signal(self, mod_ctrl_var, mod_alt_var, mod_shift_var, key_var, trigger_name):
        key = key_var.get()
        if not key:
            self._log(f"No signal configured ({trigger_name}).")
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
            self._log(f"Sent '{label}' ({trigger_name}).")
        except Exception as exc:
            self._log(f"Send error ({trigger_name}): {exc}")

    def _send_fishing_signal(self):
        self._send_signal(self.mod_ctrl_var, self.mod_alt_var, self.mod_shift_var, self.main_key_var, "Fishing")

    def _send_attack_signal(self):
        # Reset the clock, so the next automatic firing waits out the full interval (plus a
        # freshly picked attack_extra_delay, see _check_attack_timer) again from now.
        self.attack_last_used_at = time.perf_counter()
        self.attack_extra_delay = self._resolve_randomized_delay(
            self.attack_delay_var, ATTACK_DELAY_DEFAULT, self.attack_delay_range_var
        )
        self._send_signal(
            self.attack_mod_ctrl_var, self.attack_mod_alt_var, self.attack_mod_shift_var, self.attack_main_key_var,
            "Attack",
        )

    def _send_lure_signal(self):
        # Reset the clock, so the next use waits out the full delay again from now.
        # lure_fired_at additionally marks that the lure was actually sent JUST NOW - used by
        # _on_trigger_fired to ignore its own splash sound.
        self.lure_last_used_at = time.perf_counter()
        self.lure_fired_at = time.perf_counter()
        self._send_signal(
            self.lure_mod_ctrl_var, self.lure_mod_alt_var, self.lure_mod_shift_var, self.lure_main_key_var,
            "Lure",
        )

    def _send_utility_signal(self):
        # Reset the clock, so the next use waits out the full delay again from now.
        self.utility_last_used_at = time.perf_counter()
        self._send_signal(
            self.utility_mod_ctrl_var, self.utility_mod_alt_var, self.utility_mod_shift_var,
            self.utility_main_key_var, "Utility",
        )

    def _log(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")


if __name__ == "__main__":
    TriggerEditor().mainloop()
