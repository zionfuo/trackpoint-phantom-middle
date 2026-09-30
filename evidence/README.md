# Evidence: the TrackPoint's middle-button bit does not report the button

This is the raw byte stream behind the claim in the main README's
[The middle button is gone for good](../README.md#the-middle-button-is-gone-for-good).

Captured 2026-09-30 on the development machine (ThinkPad X1 Carbon Gen 8,
20UASDGA00, Ubuntu 26.04, kernel 7.0.0-34) with:

```bash
echo 1 | sudo tee /sys/module/i8042/parameters/debug
sudo dmesg -W > i8042-trace-2026-09-30.txt
# ... the three actions below ...
echo 0 | sudo tee /sys/module/i8042/parameters/debug
```

`unmask_kbd_data` was **not** touched, so every keyboard byte in the file is
`**`. Nothing you type is in here — only its timing. The TrackPoint's bytes are
never masked, which is what makes this observable at all.

## What was done during the capture

1. Push the red dot for ~8 s (no buttons).
2. Pause, hands off.
3. **Press the middle button ~5 times with the stick untouched**, pausing between
   presses.
4. Pause.
5. Type a short phrase.

## What the trace shows

```
segment 1: 10.54 s, 948 packets, 948/948 with the middle bit set
           948/948 moving, 0 keyboard bytes in the window
           --- 49.55 s of complete silence ---
segment 2: 10.73 s, 24 packets, 12 bit-set / 12 bit-clear, 0/24 moving
           all 38 keyboard bytes of the capture are in this window
```

Read it in order:

- **Movement sets the bit, always.** All 948 movement packets carry it. This
  independently reproduces an earlier run (621/621) that is not included here.
- **The button produces nothing.** Step 3 — five presses at rest — happens
  entirely inside that **49.55 s of silence**. Not one byte. If the physical
  button reached the report, this window would contain a small segment of its
  own: two packets per press, no movement, no keyboard bytes nearby. It does
  not exist.
- **The bit only clears next to keystrokes.** Segment 2 is step 5. The twelve
  bit-clear packets are interleaved one-for-one with keystrokes, in the
  `0x08 00 00` / `0x0c 00 00` alternation — the phantom. Outside a keystroke
  window, the bit is never clear.

So the bit is the firmware's own: held at 1, toggled to 0 and back only while it
synthesises a phantom packet. The physical button is not wired to it. That is
why `BTN_MIDDLE` is dropped rather than filtered cleverly — there is no signal
there to key on.

## Verify it yourself

```bash
python3 analyze.py i8042-trace-2026-09-30.txt
```

The script parses the file, frames the TrackPoint bytes into 3-byte packets,
splits them on silence, and prints the summary above. It has no defaults
tuned to this file.

To reproduce on your own machine, follow the capture commands at the top, run
the five steps, then point the script at your own trace. A healthy TrackPoint
would produce, during step 3, a segment with two packets per press — that is
the difference worth looking for.
