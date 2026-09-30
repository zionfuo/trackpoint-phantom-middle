# trackpoint-phantom-middle

**Every keypress triggers a paste on my ThinkPad.** This is the fix: keep the
TrackPoint (red dot) fully working, and drop the one phantom event that causes it.

*[中文说明 →](README.zh-CN.md)*

> **Built for, and verified on: Ubuntu 26.04 + ThinkPad X1 Carbon Gen 8**
> (20UASDGA00, kernel 7.0.0-34).
>
> The underlying cause sits in the embedded controller, below Linux, and is not
> specific to this model or this release, so other ThinkPads and other distros
> can hit the same thing. But
> that combination is what this was developed against, and the one it is known
> to work on. See [Is this your bug?](#the-root-cause) before assuming it
> applies to you.

## The symptom

On affected ThinkPads, typing in a terminal pastes the primary selection. Type
`a`, the clipboard content appears. The red dot may also feel jumpy, and keys
like Backspace/Tab can stick.

## The root cause

The keyboard (`i8042 KBD`, `serio0`) and the TrackPoint (`i8042 AUX`, `serio1`,
here a `TPPS/2 Elan TrackPoint`) **share one i8042 controller**, and the
TrackPoint's `BTN_MIDDLE` ends up an inverted **mirror image of the keyboard
event stream**:

| You do | TrackPoint reports |
|---|---|
| press a key | `BTN_MIDDLE` release |
| release a key | `BTN_MIDDLE` press |

It never settles, and each phantom press is a middle click — which on Linux
pastes the primary selection. Hence: typing pastes.

### Where it actually comes from

The obvious guess — and the one this README used to make — is that the shared
controller is mixing up the two ports. It is not. The i8042 is a faithful
courier here.

Turn on byte-level tracing and type:

```bash
echo 1 | sudo tee /sys/module/i8042/parameters/debug
sudo dmesg -w
```

Keyboard bytes arrive only as `(interrupt, 0, 1)` and TrackPoint bytes only as
`(interrupt, 1, 12)`, in strictly framed 3-byte packets, with **not one byte
delivered to the wrong port**. What shows up on the AUX port is a perfectly
well-formed mouse packet that nobody asked for: a phantom press/release pair
riding along with every keystroke.

So the packet is synthesised **below the kernel**, by the EC/TrackPoint firmware.
That is why no kernel-side or driver-side workaround has ever helped, and why
changing kernel version or flavour cannot fix it — the defect sits upstream of
everything Linux controls.

You can confirm the signature yourself, without installing anything:

```bash
sudo ./install.sh observe
```

This grabs nothing; it just logs the raw stream. Type on the keyboard and watch
`BTN_MIDDLE` toggle in lockstep with your keystrokes. If that is what you see,
this project is for you.

## The fix

`EVIOCGRAB` the TrackPoint, then re-emit it through `uinput` **event by event**,
deciding what survives:

| Event | Action | Result |
|---|---|---|
| `EV_REL` `REL_X` / `REL_Y` | forward | the red dot still moves the cursor |
| `BTN_LEFT` / `BTN_RIGHT` | forward | physical left/right click still work |
| `BTN_MIDDLE` | **dropped** | typing no longer pastes |

The desktop sees a new device named `TrackPoint Middle Filtered`; the real one
is held exclusively by the daemon.

### The trade-off, stated plainly

The phantom middle-click and a *real* middle-button press are the same event
code on the same device, so `BTN_MIDDLE` is dropped wholesale: **the physical
middle button stops working — no paste, no scroll.** That is the price of a
working red dot.

It is worth being clear that this is not a limitation of the filter that a
cleverer one could improve on. The hardware does not report this button at all —
see [The middle button is gone for good](#the-middle-button-is-gone-for-good).

If you would rather keep the middle button, the only alternative that worked
here is to not read the TrackPoint at all, which kills the red dot too.

### The middle button is gone for good

Short version: on this unit the TrackPoint's middle-button bit carries no usable
information, so there is nothing to key a filter on. Measured with byte-level
tracing (`i8042.debug=1`; TrackPoint bytes are never masked, so no keystrokes
are exposed). The raw capture and the script that slices it are in
[`evidence/`](evidence/README.md) — check the numbers rather than take them:

- Pushing the red dot for **10.5 s** produced **948 packets, every one of them
  with the middle-button bit set** — across a whole range of movement, the bit
  never cleared once. (An earlier run saw the same across 621 packets.)
- Pressing the button **at rest**, stick untouched, produced **nothing at all**:
  five presses sit inside **49.5 s of complete silence**. An earlier run put
  roughly eight presses and long holds inside 95.5 s of silence. If the button
  reached the report, each press would be its own small segment — a press packet
  and a release packet, no movement, no keystrokes nearby. No such segment
  exists in either run.
- The only packets with that bit clear are the **12 of 24** in the typing
  segment, one per keystroke — that is the phantom, not the button.

So the bit is not the button. It is asserted by *movement*, and a press at rest
does not generate a packet at all:

**the physical button is not wired to the report.** The bit belongs to the
firmware — held at 1, toggled to 0 and back only while it synthesises a phantom.

Nor is this a firmware bug waiting for a patch. This unit shipped on a 2021 BIOS
(1.17) with EC 0.1.10; we updated to Lenovo's newest release — **BIOS N2WET52W
1.42** (2026) and **EC 0.1.15** — via `fwupdmgr`, rebooted, and ran the same
byte-level capture again. Nothing changed: the bit still set through all of the
movement, the at-rest press window still completely silent, the phantom pairs
still one per keystroke. A five-year firmware gap makes no difference, which is
exactly what you would expect if the button is not wired to the report at all.

That is why dropping `BTN_MIDDLE` is not a policy choice a cleverer filter could
improve on. There is no signal to key on. Not for *hold it and push the stick to
scroll* — the gesture a middle button actually exists for — and not for a plain
at-rest click either.

### Why the usual advice does not work

All of these are **device-level switches**. Because the phantom events come from
the same device as the motion, "red dot works" and "bug is gone" are mutually
exclusive under every one of them. That is the whole reason this project exists.

| Tried | Why it fails |
|---|---|
| `blacklist psmouse` | kernel stops reading the TrackPoint — red dot dead |
| udev `LIBINPUT_IGNORE_DEVICE` | same, at the libinput layer |
| `xinput disable` | same, at the X layer |
| `gsettings ... gtk-enable-primary-paste false` | already false; GTK4/VTE implements middle-paste itself and ignores this key |
| `echo serio1 > .../psmouse/unbind` | not persistent — psmouse rebinds within seconds |
| `psmouse smartscroll=` | unrelated: that is Logitech wheel auto-repeat |

## Install

```bash
git clone https://github.com/zionfuo/trackpoint-phantom-middle
cd trackpoint-phantom-middle
./install.sh
```

`install.sh` needs `sudo`. It installs `python3-evdev`, asks the daemon which
device it would take over, renders the systemd unit with that identity, and
starts the service.

### Which device gets claimed

Matching is by **exact identity**, not by a heuristic, because the obvious
heuristics do not work here. A capability test — relative axes, a left button, no
absolute axes — is passed by the touchpad's RMI4 companion `"Mouse"` node as well
as by the TrackPoint, *and* by this project's own synthetic node. So capabilities
narrow the field, and an exact identifier settles it:

| Setting | Meaning |
|---|---|
| `TRACKPOINT_PHYS` | exact `phys`, e.g. `isa0060/serio1/input0`. The stronger claim — unique per port |
| `TRACKPOINT_NAME` | exact device name, e.g. `TPPS/2 Elan TrackPoint` |

`install.sh` writes both from what it detects. To see what it would pick, and
what it rejected and why:

```bash
./install.sh detect
```

If nothing matches, the daemon **waits and logs every input device it can see**
rather than guessing. If two devices match, it refuses outright. That is
deliberate: grabbing the wrong node disables a working touchpad and leaves the
paste bug in place, which is worse than doing nothing. Override either value if
your machine differs:

```bash
TRACKPOINT_PHYS='the phys value from ./install.sh detect' ./install.sh
TRACKPOINT_NAME='TPPS/2 IBM TrackPoint' ./install.sh
```

The identity on this machine, for reference:

```
N: Name="TPPS/2 Elan TrackPoint"
P: Phys=isa0060/serio1/input0
H: Handlers=mouse2 event6
```

Note the port: `serio1` is the AUX channel, never `serio0`, which is the
keyboard.

## Verify

```bash
./install.sh status              # unit state + recent log
journalctl -u trackpoint-phantom-middle -f
```

Check all four:

1. typing in a terminal **no longer pastes**;
2. pushing the red dot **still moves the cursor**;
3. left/right physical buttons **still work**;
4. `dropped N phantom BTN_MIDDLE events` **grows as you type** — this is what
   proves the filter is doing the work, rather than the bug having gone quiet.

Point 4 matters. The bug is intermittent-looking by nature; without the counter
you cannot distinguish "fixed" from "not happening right now".

## Troubleshooting

- **`waiting for the TrackPoint: no device matched (...)`** — the identity in the
  unit does not match your hardware. That line is followed by every input device
  the daemon can see, each with its phys and what the daemon makes of it. Copy the
  right phys into `TRACKPOINT_PHYS`, easiest via `./install.sh detect` and a
  re-run of `install.sh`. The daemon polls instead of exiting, so boot order,
  suspend and i8042 re-probe all recover on their own.
- **`... refusing to guess`** — more than one device matched, so the daemon
  deliberately did nothing. The message lists the candidates; choose one for
  `TRACKPOINT_PHYS`. This is the fail-safe working, not a malfunction.
- **`could not take over /dev/input/eventN (... Device or resource busy)`** —
  something else already holds the EVIOCGRAB: another copy of this daemon, an
  older install, or a tool like `evtest --grab`. The daemon backs off and retries
  instead of spinning; find the holder with `sudo fuser -v /dev/input/eventN`.
- **The quotes in `Environment=` are required.** systemd splits an unquoted value
  on whitespace: `Environment=TRACKPOINT_NAME=TPPS/2 Elan TrackPoint` silently
  becomes `TPPS/2`. The daemon then never matches and restart-loops.
- **`SYN_DROPPED: resynced ...`** — the kernel's event buffer overflowed under
  load and the daemon repaired the button state from the kernel's own view.
  Occasional is normal; constant means something else is starving the input loop.
- **`./install.sh observe` shows a device but no events.** An `EVIOCGRAB` is
  exclusive: while the daemon holds the TrackPoint, nobody else receives its
  events. Stop the service first
  (`sudo systemctl stop trackpoint-phantom-middle`), observe, then start it
  again. This looks like a silent failure because *opening* the node still
  succeeds — it is the events that are routed away.
- **Filing a bug?** Run `./install.sh diagnose` (or `sudo ./diagnose.sh`) and
  paste the output.

## Uninstall

```bash
./install.sh uninstall
```

Original behaviour returns immediately. This project **never** writes
`/etc/modprobe.d/` or udev rules, so it has nothing to undo beyond its own two
files. (It also cleans up `reddot-filter.*`, this project's former name.)

## What this does *not* fix

The EC firmware's phantom packet generation is untouched — it is below Linux and
cannot be patched from here. Only the middle-click symptom is filtered out. On
the machine this was developed against, two others remain:

- Backspace / Tab / T / Y / `[` / `]` / C / B can stick in the pressed state
  (visible as a terminal spewing `hhhh` or re-pasting text);
- `dmesg` shows `atkbd serio0: Unknown key pressed (translated set 2, code 0x5b)`.

The same `grab + uinput` mechanism could be pointed at the keyboard node to
debounce those keys, but the shape of a "stuck key" in the event stream has to
be characterised first. Guessing there would break typing for everyone.

## Credits and license

GPL-3.0-or-later. See [LICENSE](LICENSE).

The `grab + uinput` mechanism — and the whole idea of filtering a TrackPoint at
the event level — comes from **[zo-reddot](https://github.com/Jok0ne/zo-reddot)**
by Zerone. This project is a derivative work that inverts its policy: upstream
keeps the buttons and drops motion (dead pointer, live buttons); this keeps the
motion and drops one button (live pointer, no middle click).

Thanks are also due to the `python-evdev` project, whose object model makes this
about fifty lines of actual logic.
