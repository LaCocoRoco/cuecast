import soundcard as sc


def list_speakers():
    """Loopback-capable playback devices, indexed in soundcard's own order."""
    return list(sc.all_speakers())


def resolve_speaker(index=None):
    if index is None:
        return sc.default_speaker()
    return list_speakers()[index]


def loopback_microphone(speaker):
    """Turns a playback device into its loopback recording device."""
    return sc.get_microphone(id=str(speaker.name), include_loopback=True)
