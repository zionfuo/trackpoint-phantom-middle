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
# ── Which device we claim ──────────────────────────────────────────────────
# Matching is by exact identity, because the obvious heuristics do not work:
# a *capability* test (relative axes + BTN_LEFT, no EV_ABS) is passed by the
# touchpad's RMI4 "Mouse" companion node as well as by the TrackPoint, and by
# our own synthetic output. So capabilities filter candidates and sanity-check
# an override, and never identify a device on their own. In priority order:
#
#   TRACKPOINT_PHYS  exact .phys, e.g. isa0060/serio1/input0   (install.sh writes this)
#   TRACKPOINT_NAME  exact .name, e.g. TPPS/2 Elan TrackPoint
#   neither          autodetect: capability + name family, refusing ambiguity
#
# Ambiguity is refused rather than guessed. Grabbing the wrong node is much
# worse than grabbing none: it disables a working touchpad and leaves the bug.
#
# ── Usage ──────────────────────────────────────────────────────────────────
# systemd starts this (see trackpoint-phantom-middle.service), or run by hand:
#   sudo python3 trackpoint-phantom-middle.py              # filter, grab device
#   sudo MODE=observe python3 trackpoint-phantom-middle.py # log only, no grab
#   sudo python3 trackpoint-phantom-middle.py --detect     # say what it would claim
#
# Env:
#   TRACKPOINT_PHYS  device phys to match exactly (wins over TRACKPOINT_NAME)
#   TRACKPOINT_NAME  device name to match exactly (used if TRACKPOINT_PHYS is unset)
#   DROP_KEYS        comma-separated key names to drop, default BTN_MIDDLE
#   UINPUT_NAME      name of the replacement device the desktop sees
#   MODE             run (default) | observe | detect
import glob
import os
import signal
import sys
import time

from evdev import InputDevice, UInput, ecodes as e

LOG_TAG = "trackpoint-phantom-middle"

DEFAULT_PHYS = "isa0060/serio1/input0"
DEFAULT_NAME = "TPPS/2 Elan TrackPoint"
DEVICE_PHYS = os.environ.get("TRACKPOINT_PHYS", "").strip()
DEVICE_NAME = os.environ.get("TRACKPOINT_NAME", "").strip()
UINPUT_NAME = os.environ.get("UINPUT_NAME", "TrackPoint Middle Filtered")
MODE = os.environ.get("MODE", "run").strip().lower()

# Buttons we let through — everything else in EV_KEY is dropped.
FORWARD_BUTTONS = {e.BTN_LEFT, e.BTN_RIGHT}

# Substrings that identify a TrackPoint by name, checked case-insensitively.
# "pointing" covers the kernel's alternative name for these sticks
# (e.g. "Elan Pointing Stick"); the capability gate excludes anything else.
NAME_MARKERS = ("trackpoint", "pointing")

# Every this many dropped events we log a line (plus the first, and at shutdown).
LOG_EVERY = 20

# supervise() pacing. A run() that survives this long is considered healthy and
# clears the failure count, so a device that flaps once an hour does not inherit
# a stale backoff, while one that fails instantly does.
HEALTHY_AFTER = 30.0
POLL_INTERVAL = 2.0
BACKOFF_BASE = 1.0
BACKOFF_CAP = 10.0

# Policy outcomes — see classify().
FORWARD = "forward"
DROP = "drop"
IGNORE = "ignore"
RESYNC = "resync"


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


def describe(record) -> str:
    """One line per device, naming everything a user could copy into an override."""
    tags = []
    if record["self"]:
        tags.append("this daemon's own output")
    if record["pointer_like"]:
        tags.append("relative pointer")
    if record["trackpoint_like"]:
        tags.append("TrackPoint-like by name")
    suffix = f" [{', '.join(tags)}]" if tags else ""
    return f"{record['name']!r} ({record['path']}, phys={record['phys']!r}){suffix}"


def diagnostic_dump(records, everything: bool = False):
    """Lines describing the devices worth knowing about when a match fails.

    Only plausible candidates by default: the retry loop calls this every 30s
    while waiting for a device that may not be up yet, and dumping the entire
    input tree (both audio jacks, three buttons, the lid switch) each time buries
    the two lines that matter. --detect asks for everything, since it is a
    one-shot aid where completeness is the point.
    """
    if everything:
        chosen = records
    else:
        chosen = [r for r in records if r["pointer_like"] or r["trackpoint_like"]]
    return sorted(describe(r) for r in chosen)


def probe():
    """Describe every input node we could claim, holding none of them open.

    Returns plain records so the selection logic downstream is pure and can be
    tested without hardware. Reading /dev/input/event* needs root or the input
    group, hence the sudo in the docs.
    """
    records = []
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            dev = InputDevice(path)
        except (OSError, PermissionError):
            continue
        try:
            caps = dev.capabilities(absinfo=False)
            name = dev.name or ""
            phys = dev.phys or ""
            rel = set(caps.get(e.EV_REL, ()))
            key = set(caps.get(e.EV_KEY, ()))
            records.append({
                "path": path,
                "name": name,
                "phys": phys,
                "self": is_self(phys, name),
                # The guard rail. Necessary but NOT sufficient — the touchpad's
                # RMI4 "Mouse" companion passes this too, and so does our own
                # synthetic node, so it can filter candidates but never identify.
                #
                # Keyboards report EV_LED/EV_REP and touchpads/tablets EV_ABS;
                # requiring REL_X+REL_Y+BTN_LEFT and rejecting those three is what
                # keeps a typo from grabbing the keyboard and killing every key.
                "pointer_like": (
                    {e.REL_X, e.REL_Y} <= rel
                    and e.BTN_LEFT in key
                    and not any(t in caps for t in (e.EV_ABS, e.EV_LED, e.EV_REP))
                ),
                "trackpoint_like": any(m in name.lower() for m in NAME_MARKERS),
            })
        except OSError:
            continue
        finally:
            try:
                dev.close()
            except Exception:
                pass
    return records


def is_self(phys: str, name: str) -> bool:
    """True for the synthetic node this daemon creates.

    Load-bearing, not defensive: the synthetic node passes `pointer_like`, and
    sorted(glob('/dev/input/event*')) is lexical, so event15 sorts before event6
    and would otherwise win. Grabbing our own output freezes the pointer.
    """
    return "uinput" in (phys or "").lower() or name == UINPUT_NAME


def choose(records, want_phys: str, want_name: str):
    """Pick the one device to claim. Pure: records in, (record|None, note) out.

    Refuses on ambiguity rather than guessing. The RMI4 touchpad is the concrete
    reason: `i2c-SYNA8006:00` exposes a relative "Mouse" node that passes every
    capability test the TrackPoint does, so "pick the first relative pointer"
    would disable a working touchpad and leave the bug in place.
    """
    live = [r for r in records if not r["self"]]

    if want_phys:
        cands = [r for r in live if r["phys"] == want_phys]
        source = f"TRACKPOINT_PHYS={want_phys}"
    elif want_name:
        cands = [r for r in live if r["name"] == want_name]
        source = f"TRACKPOINT_NAME={want_name}"
    else:
        gated = [r for r in live if r["pointer_like"] and r["trackpoint_like"]]
        # Prefer real PS/2 hardware over anything else that merely qualifies.
        isa = [r for r in gated if r["phys"].startswith("isa")]
        cands, source = (isa or gated), "autodetected"

    if not cands:
        return None, f"no device matched ({source})"
    if len(cands) > 1:
        return None, (
            f"{len(cands)} devices matched ({source}); refusing to guess — set "
            f"TRACKPOINT_PHYS to one of: " + "; ".join(repr(r["phys"]) for r in cands)
        )
    only = cands[0]
    if not only["pointer_like"]:
        return None, (
            f"{only['name']!r} matched ({source}) but is not a relative pointer "
            f"with a left button; refusing to grab it"
        )
    return only, f"{source}: {only['name']!r} ({only['path']}, phys={only['phys']!r})"


def find_device():
    """Return (InputDevice, note) for the device to claim, or (None, note)."""
    record, note = choose(probe(), DEVICE_PHYS, DEVICE_NAME)
    if record is None:
        return None, note
    try:
        return InputDevice(record["path"]), note
    except (OSError, PermissionError) as exc:
        return None, f"{record['path']} matched but could not be opened ({exc})"


def classify(ev_type: int, code: int) -> str:
    """The whole forwarding policy, as a pure function.

    Kept out of the I/O loop so tests can pin the behaviour down without a
    TrackPoint, a grab, or root.
    """
    if ev_type == e.EV_SYN:
        if code == e.SYN_DROPPED:
            # Not a report: the kernel's buffer overflowed and events are
            # missing. Forwarding it would mean nothing; it needs reconciling.
            return RESYNC
        return FORWARD if code == e.SYN_REPORT else IGNORE
    if ev_type == e.EV_REL:
        # Motion goes straight through — this is what keeps the red dot alive
        # under a filter that upstream used to kill.
        return FORWARD
    if ev_type == e.EV_KEY:
        if code in DROP_KEYS:
            return DROP
        if code in FORWARD_BUTTONS:
            return FORWARD
        return IGNORE
    # Everything else (EV_MSC, EV_LED, …) is not something this device
    # produces, and we would not know how to emulate it faithfully.
    return IGNORE


class ButtonTracker:
    """Which forwarded buttons we currently believe are held.

    Once we grab the device, the compositor's button state is our word alone:
    if we forward a press and then stop (device lost, SIGTERM mid-drag), nothing
    else will ever deliver the release, and the button sticks down until the
    user clicks again.
    """

    def __init__(self, buttons):
        self._buttons = set(buttons)
        self._held = {}

    def note(self, code: int, value: int) -> None:
        if code in self._buttons:
            self._held[code] = bool(value)

    def held(self):
        return {code for code, down in self._held.items() if down}

    def reconcile(self, active_keys):
        """(release, press) sets that bring our view in line with the kernel's.

        A missed *release* is the harmful one — the button sticks down. A missed
        press is merely cosmetic, but restoring it costs nothing.
        """
        active = set(active_keys) & self._buttons
        return self.held() - active, active - self.held()


def release_held(ui, tracker: ButtonTracker) -> None:
    """Emit a release for every button we forwarded as pressed.

    Called on the way out, while uinput still exists to carry the message.
    """
    held = tracker.held()
    if not held or ui is None:
        return
    try:
        for code in sorted(held):
            ui.write(e.EV_KEY, code, 0, syn=False)
        ui.syn()
        log(
            "released " + ", ".join(kname(c, e.EV_KEY) for c in sorted(held))
            + " — still held at teardown"
        )
    except Exception:
        pass


def resync(src: InputDevice, ui: UInput, tracker: ButtonTracker) -> None:
    """Recover from SYN_DROPPED by asking the kernel what is actually held.

    Our picture of the button state may now be fiction. Without this, a press
    whose release was dropped stays down in the compositor forever.
    """
    try:
        active = src.active_keys()
    except OSError:
        return
    release, press = tracker.reconcile(active)
    if not (release or press):
        ui.syn()
        return
    try:
        for code in sorted(press):
            ui.write(e.EV_KEY, code, 1, syn=False)
            tracker.note(code, 1)
        for code in sorted(release):
            ui.write(e.EV_KEY, code, 0, syn=False)
            tracker.note(code, 0)
        ui.syn()
        log(
            f"SYN_DROPPED: resynced {len(press)} press / {len(release)} release "
            f"({', '.join(kname(c, e.EV_KEY) for c in sorted(release)) or 'none'})"
        )
    except OSError:
        pass


def observe(src: InputDevice, note: str) -> int:
    """Log the raw event stream without grabbing — diagnosis only.

    Events still reach the desktop, so the phantom paste keeps happening
    while this runs. Use it to confirm the signature on your own machine
    before trusting the fix: type on the keyboard and watch BTN_MIDDLE
    toggle in lockstep with your keystrokes.
    """
    log(f"OBSERVE {note}")
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
    except OSError as exc:
        log(f"device lost ({exc})")
    log("observe totals:")
    for (ev_type, code), n in sorted(seen.items(), key=lambda kv: -kv[1]):
        print(f"  {kname(code, ev_type):<16} {n}", file=sys.stderr)
    return 0


def claim(src: InputDevice):
    """Create the replacement device and grab the source.

    Returns (ui, None) on success, or (None, reason). The order is deliberate:
    uinput first, then grab. If uinput creation fails we still hold an ungrabbed
    pointer, so the user keeps a working cursor — the bug, but not a dead
    pointer. Grabbing first and failing to build the replacement would leave the
    real device claimed with nothing driving the cursor.

    There is no double-delivery window in this order: the synthetic device emits
    nothing until read_loop() starts, which is after the grab has landed.
    """
    # Mirror the source's axes and our chosen buttons. INPUT_PROP_POINTER is
    # what makes libinput accept the synthetic node as a pointer; we do NOT
    # copy INPUT_PROP_BUTTONPAD (the source declares it because it is an
    # Elantech unit, but without ABS axes it would only confuse clickpad
    # handling on a device libinput treats as a plain relative pointer).
    capabilities = {
        e.EV_REL: [e.REL_X, e.REL_Y],
        e.EV_KEY: sorted(FORWARD_BUTTONS),
    }
    ui = None
    try:
        ui = UInput(
            capabilities,
            name=UINPUT_NAME,
            version=0x1,
            input_props=[e.INPUT_PROP_POINTER],
        )
        src.grab()
    except OSError as exc:
        # EBUSY here means something else already holds the grab — another copy
        # of this daemon, or an older install. Report it; supervise() will back
        # off and retry rather than spin.
        if ui is not None:
            try:
                ui.close()
            except Exception:
                pass
        return None, f"could not take over {src.path} ({exc})"
    return ui, None


def run(src: InputDevice, note: str) -> int:
    """Grab the device, hand the desktop a filtered copy, drop the phantoms."""
    ui, why = claim(src)
    if ui is None:
        log(why)
        return 1

    log(f"grabbed {note}")
    log(
        f"uinput up — motion and "
        f"{', '.join(kname(c) for c in sorted(FORWARD_BUTTONS))} forwarded, "
        f"dropping {', '.join(sorted(kname(c) for c in DROP_KEYS)) or 'nothing'}"
    )

    tracker = ButtonTracker(FORWARD_BUTTONS)
    dropped = 0
    try:
        for event in src.read_loop():
            action = classify(event.type, event.code)

            if action == DROP:
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

            if action == FORWARD:
                if event.type == e.EV_KEY:
                    tracker.note(event.code, event.value)
                if event.type == e.EV_SYN:
                    ui.syn()
                else:
                    ui.write_event(event)
                continue

            if action == RESYNC:
                resync(src, ui, tracker)
                continue

            # IGNORE — nothing to do, deliberately unlogged.
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        # Device vanished — typically suspend/resume or USB re-enumeration.
        # Return non-zero so supervise() loops back and waits for it, instead
        # of leaving a dead filter behind.
        log(f"device lost ({exc}); waiting for it to come back")
        cleanup(src, ui, tracker)
        log(f"released + cleaned up ({dropped} phantom events dropped)")
        return 1

    cleanup(src, ui, tracker)
    log(f"released + cleaned up ({dropped} phantom events dropped)")
    return 0


def cleanup(src: InputDevice, ui, tracker: ButtonTracker) -> None:
    """Put the world back the way we found it.

    Order matters: release held buttons while uinput still exists to carry the
    message, then drop the replacement device, then hand the real one back.
    """
    release_held(ui, tracker)
    for step in (
        lambda: ui.close() if ui is not None else None,
        src.ungrab,
        src.close,
    ):
        try:
            step()
        except Exception:
            pass


def install_signal_handlers() -> None:
    """Turn SIGTERM into an exception so cleanup actually runs.

    Python's default disposition for SIGTERM kills the process outright,
    which skips the ungrab/uinput teardown — and, worse, the button release.
    """
    def on_term(_signum, _frame):
        raise KeyboardInterrupt

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, on_term)


def supervise() -> int:
    """Own the device, and re-acquire it whenever it goes away.

    The input node is re-created on suspend/resume and i8042 re-probe, and at
    boot it may not exist yet. Polling here keeps that in one process instead
    of exiting and letting systemd's Restart= hammer away every 2 seconds.
    """
    last_complaint = 0.0
    failures = 0
    while True:
        src, note = find_device()
        if src is None:
            now = time.monotonic()
            if now - last_complaint > 30:
                last_complaint = now
                log(f"waiting for the TrackPoint: {note}")
                for line in diagnostic_dump(probe()):
                    log(f"  {line}")
            time.sleep(POLL_INTERVAL)
            continue

        started = time.monotonic()
        # run() returns 0 on a clean shutdown (signal) and 1 when it could not
        # claim the device or lost it — in the latter case loop back and wait.
        if run(src, note) == 0:
            return 0

        # This is the only place that can spin, so the delay belongs here and
        # nowhere else. Without it, a device that is present but ungrabbable
        # (something else holds the EVIOCGRAB) would be retried in a tight loop
        # at 100% CPU, and systemd's Restart= would never get a turn.
        if time.monotonic() - started >= HEALTHY_AFTER:
            failures = 0
        failures += 1
        delay = min(BACKOFF_BASE * (2 ** (failures - 1)), BACKOFF_CAP)
        log(f"retrying in {delay:.0f}s [{note}]")
        time.sleep(delay)


def detect() -> int:
    """Print the device this daemon would claim, for install.sh to render.

    Deliberately the same code path as the runtime matcher, so the installer
    and the daemon can never disagree about which node is the TrackPoint.
    """
    records = probe()
    pick, note = choose(records, DEVICE_PHYS, DEVICE_NAME)
    if pick is None:
        log(f"cannot autodetect: {note}")
        for line in diagnostic_dump(records, everything=True):
            log(f"  {line}")
        return 1
    print(f"phys={pick['phys']}")
    print(f"name={pick['name']}")
    return 0


def main() -> int:
    install_signal_handlers()

    if "--detect" in sys.argv[1:] or MODE == "detect":
        return detect()

    if MODE == "observe":
        src, note = find_device()
        if src is None:
            log(f"cannot observe: {note}")
            for line in diagnostic_dump(probe()):
                log(f"  {line}")
            return 1
        return observe(src, note)

    try:
        return supervise()
    except KeyboardInterrupt:
        log("shutting down")
        return 0


if __name__ == "__main__":
    sys.exit(main())
