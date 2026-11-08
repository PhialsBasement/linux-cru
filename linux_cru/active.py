"""The timings the kernel is actually scanning out right now.

Compositors round the refresh rate they show (kscreen-doctor prints two
decimals) and none of them say which blanking formula a mode came from,
so near-identical modes (59.94 / 59.97 / 60.00, CVT vs CVT-RB) are hard
to tell apart. The DRM atomic state in debugfs has the exact mode each
CRTC is driving, straight from the driver:

    crtc[73]: crtc-0
            active=1
            mode: "1920x1080": 60 148500 1920 2008 2052 2200 1080 1084 1089 1125 0x48 0x5
    connector[96]: DP-1
            crtc=crtc-0

(name, vrefresh, clock kHz, h timings, v timings, type, flags.)
Reading it needs root.
"""

import glob
import re

from . import timings

_MODE = re.compile(
    r'mode:\s*"([^"]*)":\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)'
    r'\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)')

FLAG_PHSYNC = 0x1
FLAG_PVSYNC = 0x4
FLAG_INTERLACE = 0x10


def parse_state(text):
    """{connector_name: (Modeline, interlaced)} for every active output."""
    crtc_modes = {}
    conn_crtc = {}
    section = None
    name = None
    active = False
    for line in text.splitlines():
        m = re.match(r"(crtc|connector|plane)\[\d+\]:\s*(\S+)", line)
        if m:
            section, name, active = m.group(1), m.group(2), False
            continue
        s = line.strip()
        if section == "crtc":
            if s.startswith("active="):
                active = s == "active=1"
            mm = _MODE.match(s)
            if mm and active:
                v = [int(x) for x in mm.groups()[2:11]]
                flags = int(mm.group(13), 16)
                ml = timings.Modeline(
                    clock_khz=v[0],
                    hdisplay=v[1], hsync_start=v[2], hsync_end=v[3], htotal=v[4],
                    vdisplay=v[5], vsync_start=v[6], vsync_end=v[7], vtotal=v[8],
                    hsync_positive=bool(flags & FLAG_PHSYNC),
                    vsync_positive=bool(flags & FLAG_PVSYNC))
                crtc_modes[name] = (ml, bool(flags & FLAG_INTERLACE))
        elif section == "connector" and s.startswith("crtc="):
            crtc = s[len("crtc="):]
            if crtc != "(null)":
                conn_crtc[name] = crtc
    return {c: crtc_modes[k] for c, k in conn_crtc.items() if k in crtc_modes}


def _same_geometry(a, b):
    return (a.hdisplay, a.hsync_start, a.hsync_end, a.htotal,
            a.vdisplay, a.vsync_start, a.vsync_end, a.vtotal) == \
           (b.hdisplay, b.hsync_start, b.hsync_end, b.htotal,
            b.vdisplay, b.vsync_start, b.vsync_end, b.vtotal)


def identify_standard(ml):
    """Which formula produced this mode ('cvt-rb2', ...), or None."""
    r = ml.actual_refresh
    candidates = sorted({round(r), round(r, 1), round(r, 2), round(r, 3),
                         round(r * 1001 / 1000)})
    for standard in timings.STANDARDS:
        for refresh in candidates:
            try:
                calc = timings.calc(ml.hdisplay, ml.vdisplay, refresh, standard)
            except ValueError:
                continue
            if _same_geometry(calc, ml) and abs(calc.clock_khz - ml.clock_khz) <= 250:
                return standard
    return None


def read_all(debugfs="/sys/kernel/debug/dri"):
    """[(card_minor, connector, Modeline, interlaced)]; raises PermissionError."""
    out = []
    for path in sorted(glob.glob(f"{debugfs}/*/state")):
        minor = path.split("/")[-2]
        with open(path) as f:
            for conn, (ml, inter) in parse_state(f.read()).items():
                out.append((minor, conn, ml, inter))
    return out


def report(debugfs="/sys/kernel/debug/dri"):
    """Human-readable text for --active-timings."""
    import os
    parent = os.path.dirname(debugfs.rstrip("/")) or "/"
    try:
        if os.path.exists(parent) and not os.access(parent, os.R_OK | os.X_OK):
            raise PermissionError(parent)
        rows = read_all(debugfs)
    except PermissionError:
        return ("Reading the active timings needs root (they come from "
                f"{debugfs}/*/state). Run: sudo linux-cru --active-timings")
    if not rows:
        return (f"No active outputs found in {debugfs}/*/state. Is debugfs "
                "mounted (mount -t debugfs none /sys/kernel/debug)?")
    lines = []
    for minor, conn, ml, inter in rows:
        std = identify_standard(ml)
        lines.append(f"{conn} (dri/{minor}): {ml.hdisplay}x{ml.vdisplay}"
                     f"{'i' if inter else ''} @ {ml.actual_refresh:.4f} Hz, "
                     f"{ml.clock_mhz:.3f} MHz pixel clock, "
                     f"{ml.clock_khz / ml.htotal:.3f} kHz horizontal")
        lines.append(f"    Modeline {ml.timing_string()}"
                     f"{' Interlace' if inter else ''}")
        lines.append("    matches: " + (std.upper() if std else
                     "no standard formula (EDID-native or hand-made timings)"))
    return "\n".join(lines)
