#!/usr/bin/env bash
# diagnose.sh — dump everything worth pasting into a bug report.
#
# Read-only: nothing here changes system state. Some lines need root (dmesg,
# reading /dev/input/*), so run it with sudo for the complete picture.
set -uo pipefail

PROG=trackpoint-phantom-middle
section() { printf '\n===== %s =====\n' "$1"; }

section "system"
uname -a
( . /etc/os-release 2>/dev/null && echo "distro: $PRETTY_NAME" ) || true

section "input devices"
if [ -r /proc/bus/input/devices ]; then
  awk '/^I: /{dev=$0} /TrackPoint|Pointing|Mouse|Touchpad/{print dev; print; print ""}' \
    /proc/bus/input/devices 2>/dev/null || grep -B2 -i "name=" /proc/bus/input/devices
else
  echo "(cannot read /proc/bus/input/devices)"
fi

section "device names via evdev"
python3 - <<'PY' 2>/dev/null || echo "(python3-evdev not available, or no permission)"
import glob
from evdev import InputDevice, ecodes

for path in sorted(glob.glob("/dev/input/event*")):
    try:
        dev = InputDevice(path)
    except Exception as exc:
        print(f"{path}: <{exc}>")
        continue
    caps = dev.capabilities(absinfo=False)
    rel = caps.get(ecodes.EV_REL, [])
    key = caps.get(ecodes.EV_KEY, [])
    # Same tags the daemon prints when it cannot find its device, so the two
    # sections can be read against each other.
    tags = []
    if "uinput" in (dev.phys or "").lower():
        tags.append("this daemon's own output")
    if ({ecodes.REL_X, ecodes.REL_Y} <= set(rel) and ecodes.BTN_LEFT in key
            and not any(t in caps for t in (ecodes.EV_ABS, ecodes.EV_LED, ecodes.EV_REP))):
        tags.append("relative pointer")
    if "trackpoint" in dev.name.lower() or "pointing" in dev.name.lower():
        tags.append("TrackPoint-like by name")
    tail = f"  [{', '.join(tags)}]" if tags else ""
    print(f"{path}: {dev.name!r} phys={dev.phys!r} rel={rel}{tail}")
PY

section "i8042 / psmouse"
for f in /sys/devices/platform/i8042/serio*/description \
         /sys/devices/platform/i8042/serio*/firmware_id; do
  [ -r "$f" ] && echo "$f = $(cat "$f")"
done
if [ -r /sys/module/psmouse/parameters/proto ]; then
  echo "psmouse proto = $(cat /sys/module/psmouse/parameters/proto)"
fi
lsmod 2>/dev/null | grep -E '^(psmouse|i8042)' || echo "(psmouse/i8042 not listed by lsmod)"

section "leftover device-level workarounds (should be empty)"
grep -rn "psmouse" /etc/modprobe.d/ 2>/dev/null || echo "modprobe.d: clean"
grep -rln "LIBINPUT_IGNORE_DEVICE" /etc/udev/rules.d/ 2>/dev/null || echo "udev rules: clean"

section "service"
systemctl status "$PROG.service" --no-pager 2>&1 | head -20 || true
echo "--- last log lines ---"
journalctl -u "$PROG" -n 20 --no-pager 2>&1 || true

section "kernel log (i8042 / psmouse / atkbd)"
dmesg 2>/dev/null | grep -iE "i8042|psmouse|atkbd|trackpoint" | tail -30 \
  || echo "(dmesg needs root: re-run with sudo)"

section "how to interpret this"
cat <<'EOF'
Look for:
  * the device whose phys starts with 'isa' and whose name contains "TrackPoint".
    That pair is TRACKPOINT_PHYS / TRACKPOINT_NAME — phys is the stronger of the
    two, since it is unique per port while a name can be shared.
  * anything tagged "this daemon's own output" is this project's synthetic node.
    It is not the TrackPoint, and pinning it makes the daemon grab its own
    pointer and freeze the cursor. The daemon refuses to, but install.sh would
    happily write it into the unit.
  * "relative pointer" alone does NOT identify the TrackPoint. On this machine
    the Synaptics touchpad exposes an RMI4 companion "Mouse" node that passes
    every capability test the TrackPoint does.
  * the daemon's "grabbed" line — the filter is running.
  * growing "dropped N phantom BTN_MIDDLE events" lines — the filter is working.
  * "SYN_DROPPED: resynced" — the kernel dropped events under load and the
    daemon repaired the button state. Occasional is fine; constant is not.
If the daemon says it is waiting for the device, it lists what it can see right
after. Copy a phys from that list into TRACKPOINT_PHYS, or run ./install.sh
detect to see what it would pick and what it rejected.
EOF
