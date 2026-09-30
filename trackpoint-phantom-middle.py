#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
#
# trackpoint-phantom-middle — keep the TrackPoint moving, drop the phantom
# middle-click that turns every keypress into a paste.
#
# Derived from zo-reddot <https://github.com/Jok0ne/zo-reddot>
# Copyright (C) 2026 Zerone (GPL-3.0-or-later), modified 2026-09-30.
# This file is a derivative work and stays under the same license.
#
# ── What upstream did ──────────────────────────────────────────────────────
# zo-reddot grabs the TrackPoint device and re-emits ONLY the three buttons,
# dropping all motion. Result: dead pointer, live buttons.
#
# ── What we do differently, and why ────────────────────────────────────────
# On some ThinkPads (tested: X1 Carbon Gen 8 / 20UASDGA00, Ubuntu 26.04,
# kernel 7.0.0-34) the TrackPoint's BTN_MIDDLE is a *mirror image* of keyboard
# events coming over the shared i8042 controller: every keypress releases
# middle, every keyup presses it, and it never settles. A middle click pastes
# the primary selection, so typing pastes.
#
# The fix is not to disable the device — that kills the red dot too, and the
# resulting "either the pointer works or the bug is gone" dead end is what
# every device-level workaround (blacklist psmouse, LIBINPUT_IGNORE_DEVICE,
# `xinput disable`) has in common. Instead, take the device over and filter
# ONE event code on the way through. The policy is therefore inverted:
#
#   forward  EV_REL X/Y            → the red dot still moves the cursor
#   forward  BTN_LEFT, BTN_RIGHT   → the physical click buttons work
#   drop     BTN_MIDDLE            → no more phantom paste
#
# The physical middle button dies with it. That is the accepted trade-off:
# the same event code cannot distinguish a real press from a phantom one.
#
# ── Usage ──────────────────────────────────────────────────────────────────
# systemd starts this (see trackpoint-phantom-middle.service), or run by hand:
#   sudo python3 trackpoint-phantom-middle.py              # filter, grab device
#   sudo MODE=observe python3 trackpoint-phantom-middle.py # log only, no grab
#
# Env:
#   TRACKPOINT_NAME  device name to match (default below)
#   DROP_KEYS        comma-separated key names to drop, default BTN_MIDDLE
#   UINPUT_NAME      name of the replacement device the desktop sees
#   MODE             run (default) | observe
import glob
import os
import signal
import sys
import time

from evdev import InputDevice, UInput, ecodes as e

LOG_TAG = "trackpoint-phantom-middle"

DEFAULT_NAME = "TPPS/2 Elan TrackPoint"
DEVICE_NAME = os.environ.get("TRACKPOINT_NAME", DEFAULT_NAME)
UINPUT_NAME = os.environ.get("UINPUT_NAME", "TrackPoint Middle Filtered")
MODE = os.environ.get("MODE", "run").strip().lower()

# Buttons we let through — everything else in EV_KEY is dropped.
FORWARD_BUTTONS = {e.BTN_LEFT, e.BTN_RIGHT}

# Diagnostic line every this many dropped events (and at shutdown).
LOG_EVERY = 20


def log(msg: str) -> None:
    print(f"{LOG_TAG}: {msg}", file=sys.stderr, flush=True)


def parse_drop_keys(raw: str):
    """Turn 'BTN_MIDDLE,BTN_TASK' into {274, 278}; ignore unknown names."""
    codes = set()
    for name in raw.split(","):
        name = name.strip()
        if not name:
            continue
        code = getattr(e, name, None)
        if code is None:
            log(f"unknown key name '{name}', ignored")
        else:
            codes.add(code)
    return codes


DROP_KEYS = parse_drop_keys(os.environ.get("DROP_KEYS", "BTN_MIDDLE"))


def find_device(name: str):
    """Return the InputDevice whose .name matches exactly, or None."""
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            dev = InputDevice(path)
        except (OSError, PermissionError):
            continue
        if dev.name == name:
            return dev
    return None


def _flat_names(table: str):
    """ecodes.<table> as {code: name}, unwrapping the tuple entries.

    Some codes carry several names (0x110 is both BTN_LEFT and BTN_MOUSE) and
    python-evdev stores those as tuples. Keep the first.
    """
    out = {}
    for code, name in (getattr(e, table, {}) or {}).items():
        out[code] = name[0] if isinstance(name, tuple) else name
    return out


_NAMES = {t: _flat_names(t) for t in ("BTN", "KEY", "REL")}


def kname(code: int, ev_type=None) -> str:
    """Name an event code, disambiguating by type when the caller knows it.

    Codes are only unique within a type: 0 is REL_X, ABS_X and SW_LID at the
    same time, and python-evdev keeps BTN_* out of ecodes.KEY (so a button
    looked up there prints as 'code 272'). Merging the tables therefore
    mislabels — pass the event type. Bare calls are our button sets, which
    live in BTN/KEY.
    """
    if ev_type == e.EV_REL:
        return _NAMES["REL"].get(code) or f"REL code {code}"
    if ev_type == e.EV_KEY:
        return _NAMES["BTN"].get(code) or _NAMES["KEY"].get(code) or f"KEY code {code}"
    return _NAMES["BTN"].get(code) or _NAMES["KEY"].get(code) or f"code {code}"


def observe(src: InputDevice) -> int:
    """Log the raw event stream without grabbing — diagnosis only.

    Events still reach the desktop, so the phantom paste keeps happening
    while this runs. Use it to confirm the signature on your own machine
    before trusting the fix: type on the keyboard and watch BTN_MIDDLE
    toggle in lockstep with your keystrokes.
    """
    log(f"OBSERVE {src.path} ({src.name}) — ctrl-c to stop")
    log("type on the keyboard now; watching for " + ", ".join(sorted(kname(c) for c in DROP_KEYS)))
    seen = {}
    try:
        for event in src.read_loop():
            if event.type in (e.EV_KEY, e.EV_REL):
                # Keyed by (type, code): the same number means different
                # things in EV_KEY and EV_REL.
                key = (event.type, event.code)
                seen[key] = seen.get(key, 0) + 1
                if event.type == e.EV_KEY and event.code in DROP_KEYS:
                    log(
                        f"[{time.strftime('%H:%M:%S')}] "
                        f"{kname(event.code, event.type)} "
                        f"{'PRESS' if event.value else 'release'}"
                        f" +{event.timestamp():.3f}s"
                    )
    except KeyboardInterrupt:
        pass
    log("observe totals:")
    for (ev_type, code), n in sorted(seen.items(), key=lambda kv: -kv[1]):
        print(f"  {kname(code, ev_type):<16} {n}", file=sys.stderr)
    return 0


def run(src: InputDevice) -> int:
    """Grab the device, hand the desktop a filtered copy, drop the phantoms."""
    log(f"grabbing {src.path} ({src.name})")

    # Mirror the source's axes and our chosen buttons. INPUT_PROP_POINTER is
    # what makes libinput accept the synthetic node as a pointer; we do NOT
    # copy INPUT_PROP_BUTTONPAD (the source declares it because it is an
    # Elantech unit, but without ABS axes it would only confuse clickpad
    # handling on a device libinput treats as a plain relative pointer).
    capabilities = {
        e.EV_REL: [e.REL_X, e.REL_Y],
        e.EV_KEY: sorted(FORWARD_BUTTONS),
    }
    ui = UInput(
        capabilities,
        name=UINPUT_NAME,
        version=0x1,
        input_props=[e.INPUT_PROP_POINTER],
    )
    src.grab()
    log(
        f"grabbed + uinput up — motion and "
        f"{', '.join(kname(c) for c in sorted(FORWARD_BUTTONS))} forwarded, "
        f"dropping {', '.join(sorted(kname(c) for c in DROP_KEYS))}"
    )

    dropped = 0
    try:
        for event in src.read_loop():
            if event.type == e.EV_SYN:
                if event.code == e.SYN_REPORT:
                    ui.syn()
                continue

            if event.type == e.EV_REL:
                # Motion goes straight through — this is what keeps the red
                # dot alive under a filter that upstream used to kill.
                ui.write_event(event)
                continue

            if event.type == e.EV_KEY:
                if event.code in DROP_KEYS:
                    dropped += 1
                    # Log the very first one too: that line is the proof the
                    # filter is doing work, as opposed to the silly alternative
                    # explanation that the bug simply stopped happening.
                    if dropped == 1 or dropped % LOG_EVERY == 0:
                        log(
                            f"dropped {dropped} phantom "
                            f"{kname(event.code, event.type)} events so far"
                        )
                    continue
                if event.code in FORWARD_BUTTONS:
                    ui.write_event(event)
                continue
            # Everything else (EV_MSC, EV_LED, …) is not something this device
            # produces, and we would not know how to emulate it faithfully.
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        # Device vanished — typically suspend/resume or USB re-enumeration.
        # Exit non-zero so the supervisor loops back and waits for it, instead
        # of leaving a dead filter behind.
        log(f"device lost ({exc}); waiting for it to come back")
        cleanup(src, ui, dropped)
        return 1

    cleanup(src, ui, dropped)
    return 0


def cleanup(src: InputDevice, ui: UInput, dropped: int) -> None:
    try:
        src.ungrab()
    except Exception:
        pass
    try:
        ui.close()
    except Exception:
        pass
    log(f"released + cleaned up ({dropped} phantom events dropped)")


def install_signal_handlers() -> None:
    """Turn SIGTERM into an exception so cleanup actually runs.

    Python's default disposition for SIGTERM kills the process outright,
    which skips the ungrab/uinput teardown.
    """
    def on_term(_signum, _frame):
        raise KeyboardInterrupt

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, on_term)


def candidate_names():
    """Names of TrackPoint-ish devices currently present.

    Reported when the exact match fails. A renamed device after a kernel
    update is the most common way this daemon breaks, and naming what *is*
    there turns a silent restart loop into a one-line fix.
    """
    names = []
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            dev = InputDevice(path)
        except (OSError, PermissionError):
            continue
        low = dev.name.lower()
        if "trackpoint" in low or "pointing" in low:
            names.append(f"{dev.name!r} ({path})")
    return names


def supervise() -> int:
    """Own the device, and re-acquire it whenever it goes away.

    The input node is re-created on suspend/resume and i8042 re-probe, and at
    boot it may not exist yet. Polling here keeps that in one process instead
    of exiting and letting systemd's Restart= hammer away every 2 seconds.
    """
    last_complaint = 0.0
    while True:
        src = find_device(DEVICE_NAME)
        if src is None:
            now = time.monotonic()
            if now - last_complaint > 30:
                last_complaint = now
                hint = candidate_names()
                extra = (
                    " Pointing devices present: " + "; ".join(hint) + "."
                    if hint
                    else " No TrackPoint-like device present at all."
                )
                log(
                    f"waiting for '{DEVICE_NAME}' to appear.{extra} "
                    f"Override TRACKPOINT_NAME if it was renamed."
                )
            time.sleep(2)
            continue

        # run() returns 0 on a clean shutdown (signal) and 1 when the device
        # was lost — in the latter case loop back and wait for it to return.
        if run(src) == 0:
            return 0


def main() -> int:
    install_signal_handlers()

    if MODE == "observe":
        src = find_device(DEVICE_NAME)
        if src is None:
            log(
                f"no input device named '{DEVICE_NAME}' found. List devices with "
                f"'cat /proc/bus/input/devices' and override TRACKPOINT_NAME if "
                f"your TrackPoint is named differently."
            )
            return 1
        return observe(src)

    try:
        return supervise()
    except KeyboardInterrupt:
        log("shutting down")
        return 0


if __name__ == "__main__":
    sys.exit(main())
