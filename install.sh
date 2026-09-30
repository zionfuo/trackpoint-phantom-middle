#!/usr/bin/env bash
# install.sh — install / observe / diagnose / uninstall trackpoint-phantom-middle
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROG=trackpoint-phantom-middle
SCRIPT_DST="/usr/local/bin/$PROG.py"
UNIT_DST="/etc/systemd/system/$PROG.service"

# The previous name of this project. Installing removes it, so nobody ends up
# with two daemons racing for the same EVIOCGRAB (the loser just restart-loops).
LEGACY_PROG=reddot-filter
LEGACY_SCRIPT_DST="/usr/local/bin/$LEGACY_PROG.py"
LEGACY_UNIT_DST="/etc/systemd/system/$LEGACY_PROG.service"

FALLBACK_NAME="TPPS/2 Elan TrackPoint"

usage() {
  cat <<EOF
Usage: $(basename "$0") [install|observe|diagnose|status|uninstall]

install    Autodetect the TrackPoint, install python3-evdev, render and copy
           the unit + script, enable and start the service. Requires sudo.
           Default action.
observe    Run the script read-only in the foreground (no grab) so you can
           watch the raw BTN_MIDDLE stream while typing. Requires sudo.
diagnose   Print everything worth pasting into a bug report (read-only).
status     Show service state and the recent drop log.
uninstall  Stop the service, remove the files. Original behaviour returns
           immediately (this never blacklists psmouse or writes udev rules).
EOF
}

require_evdev() {
  if python3 -c "import evdev" 2>/dev/null; then
    echo "python3-evdev already present."
    return
  fi
  echo "=== Installing python3-evdev ==="
  if command -v apt-get >/dev/null; then
    sudo apt-get install -y python3-evdev
  elif command -v dnf >/dev/null; then
    sudo dnf install -y python3-evdev
  elif command -v pacman >/dev/null; then
    sudo pacman -S --noconfirm python-evdev
  else
    echo "No supported package manager. Install python3-evdev manually." >&2
    exit 1
  fi
}

# Echo the name of the first TrackPoint-ish input device, or nothing.
# Reading /dev/input/event* needs root or the input group, hence sudo.
detect_trackpoint() {
  local name
  name="$(sudo python3 - <<'PY'
import glob
from evdev import InputDevice

cands = []
for path in sorted(glob.glob("/dev/input/event*")):
    try:
        dev = InputDevice(path)
    except Exception:
        continue
    if "trackpoint" not in dev.name.lower():
        continue
    # Skip our own synthetic node. It is named "TrackPoint Middle Filtered", so
    # on a re-install — or any time an older copy of this daemon is still
    # running — it matches the name test and, sorting before the real device,
    # would otherwise win the match and get written into the unit file.
    if "uinput" in (dev.phys or "").lower():
        continue
    cands.append(dev)

# Prefer a real PS/2 device over anything else that merely mentions TrackPoint.
cands.sort(key=lambda d: (0 if (d.phys or "").startswith("isa") else 1, d.path))
if cands:
    print(cands[0].name)
PY
  )" || return 1
  [ -n "$name" ] || return 1
  printf '%s' "$name"
}

remove_legacy() {
  if [ -e "$LEGACY_UNIT_DST" ] || [ -e "$LEGACY_SCRIPT_DST" ]; then
    echo "=== Removing the older '$LEGACY_PROG' install (same fix, old name) ==="
    sudo systemctl disable --now "$LEGACY_PROG.service" 2>/dev/null || true
    sudo rm -f "$LEGACY_SCRIPT_DST" "$LEGACY_UNIT_DST"
    sudo systemctl daemon-reload
  fi
}

case "${1:-install}" in
  install)
    require_evdev
    # Cache credentials once up front so the rest of the run does not re-prompt.
    # Non-fatal: sudo-rs refuses `-v` without a controlling terminal even under
    # NOPASSWD, and that must not abort an otherwise perfectly fine install.
    sudo -v 2>/dev/null || true

    # Take down any older copy *before* autodetecting: a running one holds an
    # EVIOCGRAB on the real device and presents its own synthetic replacement,
    # which would otherwise be what gets detected.
    remove_legacy

    name="${TRACKPOINT_NAME:-}"
    if [ -z "$name" ]; then
      name="$(detect_trackpoint || true)"
    fi
    if [ -n "$name" ]; then
      echo "Detected TrackPoint: $name"
    else
      name="$FALLBACK_NAME"
      cat >&2 <<EOF
!! No TrackPoint-like device found; falling back to '$name'.
   That is fine at install time if the device is not up yet — the daemon polls
   for it — but if your machine calls it something else, re-run as:
     TRACKPOINT_NAME='Your Device Name' ./install.sh
EOF
    fi

    echo "=== Installing files ==="
    # Render the unit with the detected name. The value is always quoted for
    # the reason spelled out in the unit file: an unquoted Environment= value
    # is split on whitespace and 'TPPS/2 Elan TrackPoint' becomes 'TPPS/2'.
    unit_tmp="$(mktemp)"
    trap 'rm -f "$unit_tmp"' EXIT
    sed "s|^Environment=\"TRACKPOINT_NAME=.*\"\$|Environment=\"TRACKPOINT_NAME=$name\"|" \
      "$REPO_DIR/$PROG.service" > "$unit_tmp"
    if ! grep -q "^Environment=\"TRACKPOINT_NAME=$name\"\$" "$unit_tmp"; then
      echo "Internal error: failed to render TRACKPOINT_NAME into the unit." >&2
      exit 1
    fi

    sudo install -m 0755 "$REPO_DIR/$PROG.py" "$SCRIPT_DST"
    sudo install -m 0644 "$unit_tmp" "$UNIT_DST"
    echo "=== Enabling service ==="
    sudo systemctl daemon-reload
    sudo systemctl enable --now "$PROG.service"
    sleep 1
    sudo systemctl status "$PROG.service" --no-pager | head -8 || true

    cat <<'DONE'

Installed. The red dot still moves the cursor and left/right click still work;
BTN_MIDDLE is dropped, so typing no longer pastes.

Verify:   ./install.sh status
Watch:    journalctl -u trackpoint-phantom-middle -f   (type, watch the drops)
Rollback: ./install.sh uninstall
DONE
    ;;

  observe)
    require_evdev
    trap 'echo; echo "stopped."' EXIT
    sudo TRACKPOINT_NAME="${TRACKPOINT_NAME:-$FALLBACK_NAME}" \
         MODE=observe \
         python3 "$REPO_DIR/$PROG.py"
    ;;

  diagnose)
    "$REPO_DIR/diagnose.sh"
    ;;

  status)
    systemctl status "$PROG.service" --no-pager || true
    echo "--- recent log ---"
    journalctl -u "$PROG" -n 30 --no-pager || true
    ;;

  uninstall)
    echo "=== Stopping + disabling service ==="
    sudo systemctl disable --now "$PROG.service" 2>/dev/null || true
    sudo rm -f "$SCRIPT_DST" "$UNIT_DST"
    remove_legacy
    sudo systemctl daemon-reload
    echo "Removed. TrackPoint motion and all three buttons are back (bug included)."
    ;;

  -h|--help|help) usage ;;
  *)
    echo "Unknown action: ${1:-}" >&2
    usage >&2
    exit 2
    ;;
esac
