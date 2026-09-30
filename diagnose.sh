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
    caps = dev.capabilities(verbose=False)
    keys = caps.get(ecodes.EV_KEY, [])
    rel = caps.get(ecodes.EV_REL, [])
    print(f"{path}: {dev.name!r} phys={dev.phys} keys={len(keys)} rel={rel}")
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
  * a device whose name contains "TrackPoint" — that is TRACKPOINT_NAME
  * the daemon's "grabbed + uinput up" line — means the filter is running
  * growing "dropped N phantom BTN_MIDDLE events" lines — the filter is working
If the daemon says it is waiting for the device, the name in your unit file does
not match anything above.
EOF
