import threading
import time

import numpy as np
from scipy.signal import butter, filtfilt

from audio_devices import loopback_microphone

SAMPLE_RATE = 44100
BLOCK_SIZE = 1024

_EPS = 1e-9

# Tiefe Frequenzen (z.B. Feuer-/Raumrauschen) ignorieren wir vor der Lautstärke-Messung -
# laut Analyse liegen bei allen aufgezeichneten Splash-Sounds konsistent 30% der Energie
# unterhalb von ~950-1090Hz.
HIGHPASS_CUTOFF_HZ = 900
HIGHPASS_ORDER = 4


def compute_db(segment, sample_rate=SAMPLE_RATE):
    """Lautstärke (dBFS) eines Audio-Segments nach Hochpassfilterung.

    Gemeinsame Berechnung für die Live-Erkennung (LiveMonitor) und die dB-Anzeige einer
    manuell markierten Auswahl im Trigger Editor, damit beide exakt denselben Wert für
    dieselbe Aufnahme liefern.
    """
    if len(segment) == 0:
        return -np.inf
    b, a = butter(HIGHPASS_ORDER, HIGHPASS_CUTOFF_HZ, btype="highpass", fs=sample_rate)
    filtered = filtfilt(b, a, segment.astype(np.float64))
    rms = np.sqrt(np.mean(filtered ** 2))
    return 20 * np.log10(rms + _EPS)


class LiveMonitor:
    """Hört per Loopback auf ein Gerät und meldet einen Treffer, sobald die (hochpass-
    gefilterte) Lautstärke eines Blocks über dem Schwellwert (dBFS) liegt.

    Wir vergleichen bewusst nur die Lautstärke, nicht die exakte Signalform (kein Template-
    Abgleich mehr): reale Splash-Aufnahmen unterscheiden sich in Timing/Tonhöhe/Verlauf zu
    stark für zuverlässige Kreuzkorrelation, sind aber konsistent deutlich lauter als die
    Stille/das Hintergrundrauschen dazwischen.
    """

    def __init__(self, speaker, threshold_db, cooldown, on_trigger,
                 sample_rate=SAMPLE_RATE, block_size=BLOCK_SIZE):
        self.speaker = speaker
        self.threshold_db = threshold_db
        self.cooldown = cooldown
        self.on_trigger = on_trigger
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def set_speaker(self, speaker):
        # threshold_db/cooldown werden im Loop unten bei jedem Durchlauf frisch von self
        # gelesen - dafür reicht ein einfaches Attribut-Update von außen. Das Aufnahmegerät
        # steckt dagegen in einem bereits geöffneten Loopback-Recorder (siehe run()) und kann
        # nicht einfach ausgetauscht werden - daher hier eine eigene Methode, die die äußere
        # Schleife in run() zum Neuöffnen mit dem neuen Gerät veranlasst.
        self.speaker = speaker

    def run(self):
        last_trigger = None
        while not self.stop_event.is_set():
            current_speaker = self.speaker
            microphone = loopback_microphone(current_speaker)
            with microphone.recorder(samplerate=self.sample_rate) as recorder:
                while not self.stop_event.is_set() and self.speaker is current_speaker:
                    block = recorder.record(numframes=self.block_size)
                    mono = block.mean(axis=1) if block.ndim > 1 else block
                    db = compute_db(mono, self.sample_rate)

                    now = time.perf_counter()
                    if db >= self.threshold_db:
                        if last_trigger is None or (now - last_trigger) >= self.cooldown:
                            last_trigger = now
                            self.on_trigger(db)
