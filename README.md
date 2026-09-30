# trackpoint-phantom-middle

**Every keypress triggers a paste on my ThinkPad.** This is the fix: keep the
TrackPoint (red dot) fully working, and drop the one phantom event that causes it.

*[中文说明 →](README.zh-CN.md)*

> **Built for, and verified on: Ubuntu 26.04 + ThinkPad X1 Carbon Gen 8**
> (20UASDGA00, kernel 7.0.0-34).
>
> The underlying cause — i8042 byte crosstalk — is not specific to this model or
> this release, so other ThinkPads and other distros can hit the same thing. But
> that combination is what this was developed against, and the one it is known
> to work on. See [Is this your bug?](#the-root-cause) before assuming it
> applies to you.

## The symptom

On affected ThinkPads, typing in a terminal pastes the primary selection. Type
`a`, the clipboard content appears. The red dot may also feel jumpy, and keys
like Backspace/Tab can stick.

## The root cause

The keyboard (`i8042 KBD`, `serio0`) and the TrackPoint (`i8042 AUX`, `serio1`,
here a `TPPS/2 Elan TrackPoint`) **share one i8042 controller**. On these units
the bytes crosstalk, and the TrackPoint's `BTN_MIDDLE` ends up an inverted
**mirror image of the keyboard event stream**:

| You do | TrackPoint reports |
|---|---|
| press a key | `BTN_MIDDLE` release |
| release a key | `BTN_MIDDLE` press |

It never settles, and each phantom press is a middle click — which on Linux
pastes the primary selection. Hence: typing pastes.

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
code on the same device. They cannot be told apart, so `BTN_MIDDLE` is dropped
wholesale: **the physical middle button stops working — no paste, no scroll.**
That is the price of a working red dot. If you would rather keep the middle
button, the only alternative that worked here is to not read the TrackPoint at
all, which kills the red dot too.

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

`install.sh` needs `sudo`. It autodetects your TrackPoint's device name, installs
`python3-evdev`, renders the systemd unit and starts the service. If autodetection
fails, pass the name explicitly:

```bash
TRACKPOINT_NAME='TPPS/2 IBM TrackPoint' ./install.sh
```

The current name on this machine, for reference:

```
N: Name="TPPS/2 Elan TrackPoint"
P: Phys=isa0060/serio1/input0
H: Handlers=mouse3 event9
```

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

- **Nothing happens / log says "waiting for ... to appear".** The device name in
  the unit does not match your hardware. Find the real name with
  `cat /proc/bus/input/devices`, then either re-run `install.sh` with
  `TRACKPOINT_NAME=...` or edit `Environment="TRACKPOINT_NAME=..."` in
  `/etc/systemd/system/trackpoint-phantom-middle.service` and
  `systemctl daemon-reload && systemctl restart trackpoint-phantom-middle`.
  The daemon polls for the device instead of exiting, so boot order, suspend and
  i8042 re-probe all recover on their own.
- **The quotes in `Environment=` are required.** systemd splits an unquoted value
  on whitespace: `Environment=TRACKPOINT_NAME=TPPS/2 Elan TrackPoint` silently
  becomes `TPPS/2`. The daemon then never matches and restart-loops.
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

The i8042/EC byte crosstalk itself is untouched. Only the middle-click symptom
is filtered out. On the machine this was developed against, two others remain:

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
