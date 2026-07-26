import webbrowser

from audio_devices import list_speakers

# Verlinkt bewusst nur auf die offizielle Download-Seite, statt den Installer selbst zu
# bündeln - so bekommt man immer die aktuelle VB-CABLE-Version statt einer im Repo
# einfrierenden Kopie.
DOWNLOAD_PAGE_URL = "https://vb-audio.com/Cable/"


def is_installed():
    return any("cable" in speaker.name.lower() for speaker in list_speakers())


def open_download_page():
    webbrowser.open(DOWNLOAD_PAGE_URL)
