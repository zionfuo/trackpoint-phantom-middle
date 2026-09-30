#!/usr/bin/env bash
# install.sh — install / observe / detect / diagnose / uninstall trackpoint-phantom-middle
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROG=trackpoint-phantom-middle
SCRIPT_DST="/usr/local/bin/$PROG.py"
UNIT_DST="/etc/systemd/system/$PROG.service"

# The previous name of this project. Installing removes it, so nobody ends up
# with two daemons racing for the same EVIOCGRAB.
LEGACY_PROG=reddot-filter
LEGACY_SCRIPT_DST="/usr/local/bin/$LEGACY_PROG.py"
LEGACY_UNIT_DST="/etc/systemd/system/$LEGACY_PROG.service"

# Written into the unit when autodetection finds nothing. That is the normal
# state on a fresh boot before the input stack is up, so it is a soft failure.
FALLBACK_PHYS="isa0060/serio1/input0"
FALLBACK_NAME="TPPS/2 Elan TrackPoint"

usage() {
  cat <<EOF
Usage: $(basename "$0") [install|observe|detect|diagnose|status|uninstall]

install    Autodetect the TrackPoint, install python3-evdev, render and copy
           the unit + script, enable and start the service. Requires sudo.
           Default action.
observe    Run the script read-only in the foreground (no grab) so you can
           watch the raw BTN_MIDDLE stream while typing. Requires sudo.
detect     Print the device the daemon would claim, and every other input
           device it considered. Requires sudo.
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

# Echo "phys<TAB>name" for the device to claim, or nothing.
#
# This deliberately shells out to the daemon's own --detect instead of scanning
# /dev/input itself. The installer and the daemon have to agree on which node is
# the TrackPoint — if they disagree, the installer pins one device and the
# daemon watches another, and nothing says so. Running the same code is the only
# way to guarantee they cannot drift apart.
# Reading /dev/input/event* needs root or the input group, hence sudo.
detect_trackpoint() {
  local out phys name
  out="$(sudo python3 "$REPO_DIR/$PROG.py" --detect)" || return 1
  phys="$(printf '%s\n' "$out" | sed -n 's/^phys=//p')"
  name="$(printf '%s\n' "$out" | sed -n 's/^name=//p')"
  [ -n "$phys" ] || return 1
  printf '%s\t%s' "$phys" "$name"
}

# Escape a value for use on the replacement side of a sed s||| command.
sed_replacement() {
  printf '%s' "$1" | sed -e 's/[&\\|]/\\&/g'
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
    # which would otherwise be what gets detected. (The daemon also refuses to
    # grab its own output, but the detection would still be misled.)
    remove_legacy

    phys="${TRACKPOINT_PHYS:-}"
    name="${TRACKPOINT_NAME:-}"
    if [ -z "$phys" ] && [ -z "$name" ]; then
      detected="$(detect_trackpoint || true)"
      if [ -n "$detected" ]; then
        phys="${detected%%$'\t'*}"
        name="${detected#*$'\t'}"
      fi
    fi

    if [ -n "$phys" ]; then
      echo "Detected TrackPoint: $name ($phys)"
    elif [ -n "$name" ]; then
      echo "Using TRACKPOINT_NAME=$name (no phys pinned; matching by name)"
    else
      phys="$FALLBACK_PHYS"
      name="$FALLBACK_NAME"
      cat >&2 <<EOF
!! No TrackPoint-like device found; falling back to '$name' ($phys).
   That is fine at install time if the device is not up yet — the daemon polls
   for it, and logs what it does find. If your machine calls it something else,
   re-run with one of:
     TRACKPOINT_PHYS='the phys value from ./install.sh detect' ./install.sh
     TRACKPOINT_NAME='Your Device Name' ./install.sh
EOF
    fi

    echo "=== Installing files ==="
    # Render the unit with the detected identity. Both values are always quoted,
    # for the reason spelled out in the unit file: an unquoted Environment= value
    # is split on whitespace and 'TPPS/2 Elan TrackPoint' becomes 'TPPS/2'.
    # An empty phys is fine — the daemon reads that as unset and falls back to
    # matching on the name.
    unit_tmp="$(mktemp)"
    trap 'rm -f "$unit_tmp"' EXIT
    sed -e "s|^Environment=\"TRACKPOINT_PHYS=.*\"\$|Environment=\"TRACKPOINT_PHYS=$(sed_replacement "$phys")\"|" \
        -e "s|^Environment=\"TRACKPOINT_NAME=.*\"\$|Environment=\"TRACKPOINT_NAME=$(sed_replacement "$name")\"|" \
      "$REPO_DIR/$PROG.service" > "$unit_tmp"
    if ! grep -q "^Environment=\"TRACKPOINT_PHYS=$phys\"\$" "$unit_tmp"; then
      echo "Internal error: failed to render TRACKPOINT_PHYS into the unit." >&2
      exit 1
    fi
    if ! grep -q "^Environment=\"TRACKPOINT_NAME=$name\"\$" "$unit_tmp"; then
      echo "Internal error: failed to render TRACKPOINT_NAME into the unit." >&2
      exit 1
    fi

    sudo install -m 0755 "$REPO_DIR/$PROG.py" "$SCRIPT_DST"
    sudo install -m 0644 "$unit_tmp" "$UNIT_DST"
    echo "=== Enabling service ==="
    sudo systemctl daemon-reload
    sudo systemctl enable "$PROG.service"
    # Restart, not `enable --now`: start is a no-op on an already-active unit, so
    # upgrading an existing install would leave the OLD code running until the
    # next reboot, while the log and the file on disk both said otherwise.
    sudo systemctl restart "$PROG.service"
    sleep 1
    sudo systemctl status "$PROG.service" --no-pager | head -8 || true

    cat <<'DONE'

Installed. The red dot still moves the cursor and left/right click still work;
BTN_MIDDLE is dropped, so typing no longer pastes.

Verify:   ./install.sh status
Check:    ./install.sh detect      (what the daemon claims, and what it rejected)
Watch:    journalctl -u trackpoint-phantom-middle -f   (type, watch the drops)
Rollback: ./install.sh uninstall
DONE
    ;;

  observe)
    require_evdev
    trap 'echo; echo "stopped."' EXIT
    # Pass the overrides through if the caller set them; otherwise stay out of
    # the way and let the daemon autodetect with its own matcher.
    sudo TRACKPOINT_PHYS="${TRACKPOINT_PHYS:-}" \
         TRACKPOINT_NAME="${TRACKPOINT_NAME:-}" \
         MODE=observe \
         python3 "$REPO_DIR/$PROG.py"
    ;;

  detect)
    require_evdev
    sudo TRACKPOINT_PHYS="${TRACKPOINT_PHYS:-}" \
         TRACKPOINT_NAME="${TRACKPOINT_NAME:-}" \
         python3 "$REPO_DIR/$PROG.py" --detect
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
