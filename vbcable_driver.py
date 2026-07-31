import webbrowser

from audio_devices import list_speakers

# Deliberately just links to the official download page instead of bundling the installer
# itself - this way you always get the current VB-CABLE version instead of a copy that goes
# stale in the repo.
DOWNLOAD_PAGE_URL = "https://vb-audio.com/Cable/"


def is_installed():
    return any("cable" in speaker.name.lower() for speaker in list_speakers())


def open_download_page():
    webbrowser.open(DOWNLOAD_PAGE_URL)
