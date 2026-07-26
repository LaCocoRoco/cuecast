import argparse
import time
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from audio_devices import list_speakers, loopback_microphone, resolve_speaker

SAMPLE_RATE = 44100
BLOCK_SIZE = 1024
SNIPPET_DIR = Path(__file__).parent / "snippets"


def print_devices():
    for index, speaker in enumerate(list_speakers()):
        print(f"[{index}] {speaker.name}")


def record(speaker, stop_event=None):
    microphone = loopback_microphone(speaker)
    blocks = []
    stop_hint = "Stop-Button" if stop_event is not None else "Ctrl+C"
    print(f"Aufnahme von '{speaker.name}' läuft. Zum Stoppen: {stop_hint}")
    with microphone.recorder(samplerate=SAMPLE_RATE) as recorder:
        try:
            while stop_event is None or not stop_event.is_set():
                blocks.append(recorder.record(numframes=BLOCK_SIZE))
        except KeyboardInterrupt:
            pass
    return np.concatenate(blocks, axis=0) if blocks else np.zeros((0, 1), dtype=np.float32)


def save_wav(data):
    mono = data.mean(axis=1) if data.ndim > 1 else data
    pcm16 = np.clip(mono, -1.0, 1.0)
    pcm16 = (pcm16 * 32767).astype(np.int16)

    SNIPPET_DIR.mkdir(exist_ok=True)
    path = SNIPPET_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}.wav"
    wavfile.write(path, SAMPLE_RATE, pcm16)
    return path


def main():
    parser = argparse.ArgumentParser(description="Audio-Snippet über Loopback aufnehmen.")
    parser.add_argument("--list-devices", action="store_true", help="Verfügbare Geräte anzeigen und beenden.")
    parser.add_argument("--device", type=int, default=None, help="Geräte-Index aus --list-devices.")
    args = parser.parse_args()

    if args.list_devices:
        print_devices()
        return

    speaker = resolve_speaker(args.device)
    data = record(speaker)
    path = save_wav(data)
    print(f"Gespeichert: {path}")


if __name__ == "__main__":
    main()
