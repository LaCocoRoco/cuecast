# Cuecast

Audio-threshold-based trigger tool: listens to loopback audio, and reacts to loud events by
sending configured key combinations (via the Interception HID driver). Originally built
around a WoW fishing use case, hence the "Fishing" / "Lure" / "Attack" terminology.

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
  -> Fishing Trigger: cast

Threshold detected
  -> Fishing Trigger: reel in
  -> if Lure Delay > 0 and enough time has passed since the last Lure use:
       -> fixed pause
       -> Lure Trigger: use lure
       -> fixed pause (own delay, independent of the one above - the lure
          itself takes a moment to actually land/apply)
       -> ignore the Threshold for 2s from the moment the lure was used (it
          makes a splash when it hits the water, which would otherwise
          immediately re-trigger this whole sequence)
  -> fixed pause
  -> Fishing Trigger: cast again

Timeout (no bite within the configured time)
  -> Fishing Trigger: cast (acts like a toggle - if still "fishing", this
     interrupts it; the loop naturally recovers on the next cycle)
```

Currently deliberately free of any randomized/humanized delays - those had led to
inconsistent results, so the minimal reliable timings are being determined first; randomness
can be reintroduced later once those are known.

Attack Trigger runs completely independently of the above: as long as its own interval is
set above 0, it just presses its configured key on that interval, the whole time monitoring
is active.

## Settings

All key combinations (modifiers + main key), the Threshold, and the various timing values
(Fishing Timeout, Lure Delay, Attack Interval) are configurable in the UI and persisted in
`settings.json`, keyed per value - a value missing from a fresh `settings.json` simply falls
back to its built-in default.
