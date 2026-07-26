# Audioanalyse

Template-/Kreuzkorrelations-Matching

# Eingabe

Virtual HID Treiber

# Eingabe Alternative

PostMessage/SendInput (Wird vorerst nicht verwendet)

# Ansatz Idee Aufwand Robustheit

Fixer Schwellwert (aktuell)
Lautstärke > konstanter dB-Wert
minimal niedrig
jedes laute Geräusch triggert

# Adaptiver/relativer Schwellwert

Trigger bei "X dB über gleitendem Durchschnitt/Median der letzten Sekunden" statt festem Wert
gering
mittel – passt sich an Umgebungslautstärke an, löst aber immer noch bei jedem Geräusch aus

# Bandpass-gefilterte Energie

Nur den Frequenzbereich analysieren, in dem der Splash-Sound tatsächlich Energie hat (FFT/Filter vor RMS)
mittel
mittel-hoch – filtert z.B. tiefe Musik-Bässe oder hohe UI-Pings raus

# Template-/Kreuzkorrelations-Matching

Einmal eine Referenzaufnahme des echten Splash-Sounds machen, eingehende Blöcke per Kreuzkorrelation dagegen vergleichen
mittel-hoch
hoch – erkennt spezifisch dieses Geräusch statt "irgendwas Lautes"

# Onset-/Spectral-Flux-Detection (z.B. librosa)

Aus der Musikanalyse geliehene Verfahren zur Erkennung plötzlicher Klangeinsätze
hoch
hoch, aber mehr Abhängigkeiten/Rechenaufwand

# Bildbasiert statt Audio

Screenshot/Pixelanalyse auf die Angel-Bobber-Animation statt Ton
mittel-hoch
umgeht das Audio-Problem komplett, dafür empfindlich gegenüber Kamerawinkel/UI-Position
