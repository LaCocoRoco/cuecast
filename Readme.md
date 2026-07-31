# Cuecast

Audio-threshold-based trigger tool: listens to loopback audio, and reacts to loud events by
sending configured key combinations (via the Interception HID driver). Originally built
around a WoW fishing use case, hence the "Angel" (fishing rod) / "Lure" / "Attack"
terminology.

## Audio detection

A block of incoming audio is high-pass filtered (removes low-frequency background noise like
ambient rumble/fire crackling) and compared against a fixed dB threshold ("Threshold" in the
UI). No waveform/template matching - real in-game sounds vary too much in timing/pitch for
reliable cross-correlation, but are consistently louder than the silence/background noise
between them.

## The main loop

```
Start
  -> short warm-up (5s, fixed) before the timeout routine engages
  -> Angel Trigger: cast

Threshold detected
  -> wait 0-1s (randomized, feels more human than an instant reaction)
  -> Angel Trigger: reel in
  -> if Lure Delay > 0 and enough time has passed since the last Lure use:
       -> Lure Trigger: use lure
       -> ignore the Threshold for 2s afterwards (the lure itself makes a splash
          when it hits the water, which would otherwise immediately re-trigger
          this whole sequence)
  -> fixed pause (1s)
  -> Angel Trigger: cast again

Timeout (no bite within the configured time)
  -> Angel Trigger: cast (acts like a toggle - if still "fishing", this
     interrupts it; the loop naturally recovers on the next cycle)
```

Attack Trigger runs completely independently of the above: as long as its own interval is
set above 0, it just presses its configured key on that interval, the whole time monitoring
is active.

## Settings

All key combinations (modifiers + main key), the Threshold, and the various timing values
(Angel Timeout, Lure Delay, Attack Interval) are configurable in the UI and persisted in
`settings.json`, keyed per value - a value missing from a fresh `settings.json` simply falls
back to its built-in default.
