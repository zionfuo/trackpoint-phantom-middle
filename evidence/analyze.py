#!/usr/bin/env python3
"""Slice an i8042 byte trace into activity segments and summarise each one.

Capturing a trace:

    sudo sh -c 'echo 1 > /sys/module/i8042/parameters/debug'
    sudo dmesg -W > trace.txt      # -W, not -w: follow new messages only
    ...exercise the TrackPoint...
    sudo sh -c 'echo 0 > /sys/module/i8042/parameters/debug'

Use `-W`. `-w` dumps the entire ring buffer before following, which drags
unrelated earlier lines into your capture -- and if `unmask_kbd_data` was ever
switched on, that includes plaintext keystrokes from whenever it was on.

Keyboard bytes are printed as `**` unless `unmask_kbd_data` is enabled. Nothing
here needs them: this script only counts them, so a capture stays free of
keystroke content.

    python3 analyze.py i8042-trace-2026-09-30.txt
"""

import collections
import re
import sys

LINE = re.compile(
    r'^\[\s*(\d+\.\d+)\]\s+i8042:\s+\[\s*\d+\]\s+(\*\*|[0-9a-f]{2})\s+'
    r'<-\s+i8042\s+\(([^)]*)\)'
)

AUX = 'interrupt, 1, 12'      # TrackPoint (serio1, IRQ 12)
KBD = 'interrupt, 0, 1'       # keyboard (serio0, IRQ 1)

PACKET_GAP = 0.020            # bytes further apart than this start a new packet
SEGMENT_GAP = 1.5             # seconds of silence that separate activity segments

# PS/2 mouse packet byte 0: bit3 always 1, bit2 = middle, bit1 = right, bit0 = left,
# bit4/5 = sign of X/Y. So 0x08 = nothing pressed, 0x0c = middle pressed.
BTN_MIDDLE = 0x04


def parse(path):
    aux, kbd = [], []
    for line in open(path, errors='replace'):
        m = LINE.match(line)
        if not m:
            continue
        t = float(m.group(1))
        byte = None if m.group(2) == '**' else int(m.group(2), 16)
        where = m.group(3)
        if where == AUX:
            aux.append((t, byte))
        elif where == KBD:
            kbd.append((t, byte))
    return aux, kbd


def packets(aux):
    """Group AUX bytes into 3-byte packets, breaking on gaps and ragged runs."""
    out, cur, last = [], [], None
    for t, b in aux:
        if last is not None and t - last > PACKET_GAP:
            out.extend(chunk(cur))
            cur = []
        cur.append((t, b))
        last = t
    out.extend(chunk(cur))
    return out


def chunk(run):
    if len(run) % 3:
        print(f"  !! ragged run of {len(run)} bytes ending {run[-1][0]:.3f}", file=sys.stderr)
    return [run[i:i + 3] for i in range(0, len(run) - len(run) % 3, 3)]


def segments(pkts):
    out, cur = [], []
    for p in pkts:
        if cur and p[0][0] - cur[-1][2][0] > SEGMENT_GAP:
            out.append(cur)
            cur = []
        cur.append(p)
    if cur:
        out.append(cur)
    return out


def main(path):
    aux, kbd = parse(path)
    pkts = packets(aux)
    if not pkts:
        print("no TrackPoint traffic in this capture")
        return
    segs = segments(pkts)

    print(f"{path}")
    print(f"  {len(aux)} TrackPoint bytes -> {len(pkts)} packets, "
          f"{len(kbd)} keyboard bytes ({sum(1 for _, b in kbd if b is not None)} unmasked)\n")

    for i, seg in enumerate(segs, 1):
        t0, t1 = seg[0][0][0], seg[-1][2][0]
        byte0 = collections.Counter(p[0][1] for p in seg)
        moving = sum(1 for p in seg if p[1][1] or p[2][1])
        middle = sum(1 for p in seg if p[0][1] & BTN_MIDDLE)
        quiet = sum(1 for p in seg if not p[0][1] & BTN_MIDDLE)
        near = sum(1 for t, _ in kbd if t0 - 0.05 <= t <= t1 + 0.05)
        gap = t0 - segs[i - 2][-1][2][0] if i > 1 else None
        print(f"  segment {i}: {t0:.3f}..{t1:.3f}  ({t1 - t0:5.2f} s, {len(seg)} packets)"
              + (f"   after {gap:.2f} s of silence" if gap else ""))
        print(f"      byte0 = {{{', '.join(f'0x{k:02x}: {v}' for k, v in sorted(byte0.items()))}}}")
        print(f"      moving packets : {moving}/{len(seg)}")
        print(f"      middle bit set : {middle}   clear: {quiet}")
        print(f"      keyboard bytes in window: {near}")
    print("\nReading it: a middle-button press at rest would show up as its own segment")
    print("-- a press then a release, no keyboard bytes nearby. Movement asserting the")
    print("middle bit throughout, and bit-clear packets appearing only next to keyboard")
    print("bytes, both mean the bit is not reporting the button.")


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'i8042-trace-2026-09-30.txt')
