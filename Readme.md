# Cuecast

Vibe-coded with Claude. I didn't hand-write a line of it, and I don't care. Use at your own risk.

Audio-threshold-based trigger tool: listens to loopback audio, and reacts to loud events by
sending configured key combinations.

## Why HID

Key presses go exclusively through the [Interception](https://github.com/oblitum/Interception)
kernel driver, not `SendMessage`/`PostMessage`. Interception acts as a real keyboard at the
driver level, so input is indistinguishable from an actual keypress - unlike injected window
messages, which are trivially detectable as not coming from real hardware.

## Audio detection

A block of incoming audio is high-pass filtered (removes low-frequency background noise like
ambient rumble/fire crackling) and compared against a fixed dB threshold ("Threshold" in the
UI). No waveform/template matching - real in-game sounds vary too much in timing/pitch for
reliable cross-correlation, but are consistently louder than the silence/background noise
between them.

## Recording & Threshold

Record a short snippet of loopback audio, mark the loud part in the waveform, and hit "Apply
as Threshold" - it suggests a dB value just below the loudest block in the selection, so you
don't have to guess a Threshold by hand.

## Triggers

- **Fishing** - fires on a real Threshold detection, and once again if no bite comes within
  the configured timeout. ⓘ hints at installing the "Better Fishing" addon.
- **Lure** - not a timer of its own; fires as part of a real bite once its delay has elapsed
  since the last use.
- **Utility** - works exactly like Lure, just with its own key/delay.
- **Attack** - fully independent, fires on its own fixed interval the whole time monitoring
  is active. ⓘ hints at enabling Action Targeting so it doesn't interrupt fishing.

## Timing

The "Timing" dialog exposes the fine delays between each step (plus an optional randomized
range for each) and the Start warm-up. On a slower PC, these may need to be increased - a
sluggish system can make sends/detection lag behind the defaults tuned on a faster one.

## Settings

All key combinations (modifiers + main key), the Threshold, and the various timing values
are configurable in the UI and persisted in `settings.json`, keyed per value - a value
missing from a fresh `settings.json` simply falls back to its built-in default.
