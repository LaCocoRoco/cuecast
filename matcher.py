import threading
import time

import numpy as np
from scipy.signal import butter, filtfilt

from audio_devices import loopback_microphone

SAMPLE_RATE = 44100
BLOCK_SIZE = 1024

_EPS = 1e-9

# We ignore low frequencies (e.g. fire/ambient room noise) before measuring loudness - per
# analysis, all recorded splash sounds consistently have 30% of their energy below
# ~950-1090Hz.
HIGHPASS_CUTOFF_HZ = 900
HIGHPASS_ORDER = 4


def compute_db(segment, sample_rate=SAMPLE_RATE):
    """Loudness (dBFS) of an audio segment after high-pass filtering.

    Shared calculation for live detection (LiveMonitor) and the dB display of a manually
    marked selection in the Trigger Editor, so both produce exactly the same value for the
    same recording.
    """
    if len(segment) == 0:
        return -np.inf
    b, a = butter(HIGHPASS_ORDER, HIGHPASS_CUTOFF_HZ, btype="highpass", fs=sample_rate)
    filtered = filtfilt(b, a, segment.astype(np.float64))
    rms = np.sqrt(np.mean(filtered ** 2))
    return 20 * np.log10(rms + _EPS)


def compute_peak_db(segment, sample_rate=SAMPLE_RATE, block_size=BLOCK_SIZE):
    """Loudest block (same block size as live detection) within a segment.

    Deliberately neither an RMS average over the whole (possibly quiet-edged) segment -
    that would dilute a real hit - nor the absolute sample peak - that would be vulnerable to
    a single outlier. The block size matches exactly the granularity LiveMonitor later
    compares against, so a single outlier is averaged out within a ~23ms window.
    """
    if len(segment) < block_size:
        return compute_db(segment, sample_rate)
    best_db = -np.inf
    for start in range(0, len(segment) - block_size + 1, block_size):
        block_db = compute_db(segment[start:start + block_size], sample_rate)
        if block_db > best_db:
            best_db = block_db
    return best_db


class LiveMonitor:
    """Listens to a device via loopback and reports a hit as soon as a block's (high-pass
    filtered) loudness exceeds the threshold (dBFS).

    We deliberately only compare loudness, not the exact waveform shape (no more template
    matching): real splash recordings vary too much in timing/pitch/shape for reliable
    cross-correlation, but are consistently much louder than the silence/background noise
    between them.
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
        # threshold_db/cooldown are read fresh from self on every loop iteration below - a
        # plain attribute update from outside is enough for those. The recording device, on
        # the other hand, is tied to an already-open loopback recorder (see run()) and can't
        # simply be swapped out - hence this dedicated method, which makes the outer loop in
        # run() reopen with the new device.
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
