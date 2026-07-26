import soundcard as sc


def list_speakers():
    """Loopback-fähige Ausgabegeräte, indiziert in der Reihenfolge von soundcard."""
    return list(sc.all_speakers())


def resolve_speaker(index=None):
    if index is None:
        return sc.default_speaker()
    return list_speakers()[index]


def loopback_microphone(speaker):
    """Wandelt ein Ausgabegerät in sein Loopback-Aufnahmegerät um."""
    return sc.get_microphone(id=str(speaker.name), include_loopback=True)
