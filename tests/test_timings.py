"""Timing engine tests.

Run directly (python tests/test_timings.py) or via pytest. If the xorg
cvt(1) binary is installed, CVT and CVT-RB outputs are additionally
compared against it across a grid of resolutions/refresh rates.
"""

import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from linux_cru import timings


def _values(ml):
    return (ml.hdisplay, ml.hsync_start, ml.hsync_end, ml.htotal,
            ml.vdisplay, ml.vsync_start, ml.vsync_end, ml.vtotal)


def test_cvt_1080p60_matches_xorg_reference():
    # xorg cvt 1920 1080 60:
    # Modeline "1920x1080_60.00"  173.00  1920 2048 2248 2576  1080 1083 1088 1120 -hsync +vsync
    ml = timings.cvt(1920, 1080, 60)
    assert ml.clock_khz == 173000, ml
    assert _values(ml) == (1920, 2048, 2248, 2576, 1080, 1083, 1088, 1120), ml
    assert ml.flags == "-hsync +vsync"


def test_cvt_rb_1080p60_matches_xorg_reference():
    # xorg cvt -r 1920 1080 60:
    # Modeline "1920x1080R"  138.50  1920 1968 2000 2080  1080 1083 1088 1111 +hsync -vsync
    ml = timings.cvt_rb(1920, 1080, 60)
    assert ml.clock_khz == 138500, ml
    assert _values(ml) == (1920, 1968, 2000, 2080, 1080, 1083, 1088, 1111), ml
    assert ml.flags == "+hsync -vsync"


def test_cvt_rb2_reproduces_samsung_s95b_4k144():
    # The known-good community modeline for the Samsung S95B at 4K@144
    # (previously hardcoded in this app) is exactly CVT-RB2:
    # 1306.206 3840 3848 3880 3920 2160 2300 2308 2314 +hsync -vsync
    ml = timings.cvt_rb2(3840, 2160, 144)
    assert ml.clock_khz == 1306206, ml
    assert _values(ml) == (3840, 3848, 3880, 3920, 2160, 2300, 2308, 2314), ml
    assert ml.flags == "+hsync -vsync"


def test_cvt_rb_1080p71_matches_overclocking_guide():
    # kevinlekiller's Intel overclocking guide: 1080p@71 reduced blanking
    # is ~164.96 MHz.
    ml = timings.cvt_rb(1920, 1080, 71)
    assert ml.clock_khz == 164750 or abs(ml.clock_khz - 164960) < 300, ml
    # exact per formula: 71 * 1117 * 2080 = 164.96 MHz, floored to 250 kHz
    assert ml.vtotal == 1117 and ml.htotal == 2080, ml


def test_gtf_matches_reference_modelines():
    """GTF is what CRT monitors were designed around; xorg gtf(1) output."""
    for (w, h, r), expected in [
            ((1024, 768, 75),
             "81.80 1024 1080 1192 1360 768 769 772 802 -hsync +vsync"),
            ((1280, 1024, 60),
             "108.88 1280 1360 1496 1712 1024 1025 1028 1060 -hsync +vsync"),
            ((1920, 1080, 60),
             "172.80 1920 2040 2248 2576 1080 1081 1084 1118 -hsync +vsync")]:
        got = timings.gtf(w, h, r).timing_string()
        assert got == expected, f"{w}x{h}@{r}\n  want {expected}\n  got  {got}"


def test_gtf_blanking_is_generous_enough_for_a_crt():
    """The reason a CRT needs GTF: reduced blanking leaves too little
    retrace time, which is what pushes the picture off the screen."""
    crt = timings.gtf(1024, 768, 75)
    rb = timings.cvt_rb2(1024, 768, 75)
    assert (crt.htotal - crt.hdisplay) > 2 * (rb.htotal - rb.hdisplay)
    assert (crt.vtotal - crt.vdisplay) > (rb.vtotal - rb.vdisplay)


def test_rb2_invariants():
    for (w, h, r) in [(1920, 1080, 75), (2560, 1440, 165), (3840, 2160, 120),
                      (1366, 768, 60), (5120, 1440, 90), (1920, 1080, 59.94)]:
        ml = timings.cvt_rb2(w, h, r)
        assert ml.htotal - ml.hdisplay == 80
        assert ml.hsync_start - ml.hdisplay == 8
        assert ml.hsync_end - ml.hsync_start == 32
        assert ml.vsync_end - ml.vsync_start == 8          # fixed vsync
        assert ml.vtotal - ml.vsync_end == 6               # fixed back porch
        assert ml.vsync_start > ml.vdisplay                # front porch >= 1
        assert abs(ml.actual_refresh - r) < 0.01, (w, h, r, ml.actual_refresh)


def test_refresh_too_high_raises():
    try:
        timings.cvt_rb2(3840, 2160, 5000)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_calc_dispatch():
    assert timings.calc(1920, 1080, 60, "cvt").clock_khz == 173000
    assert timings.calc(1920, 1080, 60, "cvt-rb").clock_khz == 138500
    assert timings.calc(1920, 1080, 60, "gtf").clock_khz == 172800
    assert set(timings.STANDARDS) == {"cvt", "cvt-rb", "cvt-rb2", "gtf"}
    try:
        timings.calc(1920, 1080, 60, "nonsense")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


# -- differential test against the real cvt(1) binary -----------------------

_CVT_RE = re.compile(
    r'Modeline\s+"[^"]+"\s+([\d.]+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)'
    r"\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([+-]hsync)\s+([+-]vsync)", re.I)

GRID = [(1024, 768, 60), (1280, 1024, 75), (1920, 1080, 60), (1920, 1080, 75),
        (1920, 1200, 60), (2560, 1080, 60), (2560, 1440, 60), (2560, 1440, 75),
        (3440, 1440, 60), (3840, 2160, 30), (3840, 2160, 60), (1600, 900, 60)]


def _xorg_cvt(w, h, r, reduced):
    cmd = ["cvt"] + (["-r"] if reduced else []) + [str(w), str(h), str(r)]
    out = subprocess.check_output(cmd, universal_newlines=True,
                                  stderr=subprocess.DEVNULL)
    m = _CVT_RE.search(out)
    assert m, out
    g = m.groups()
    return (int(float(g[0]) * 1000), tuple(int(x) for x in g[1:9]),
            f"{g[9].lower()} {g[10].lower()}")


def test_against_cvt_binary():
    if not shutil.which("cvt"):
        print("cvt(1) not installed — skipping differential test")
        return
    for (w, h, r) in GRID:
        for reduced in (False, True):
            if reduced and r % 60 != 0:
                continue  # cvt -r only accepts multiples of 60
            clock, vals, flags = _xorg_cvt(w, h, r, reduced)
            ml = timings.cvt_rb(w, h, r) if reduced else timings.cvt(w, h, r)
            assert _values(ml) == vals, (w, h, r, reduced, _values(ml), vals)
            assert ml.clock_khz == clock, (w, h, r, reduced, ml.clock_khz, clock)
            assert ml.flags == flags, (w, h, r, reduced, ml.flags, flags)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failures else 0)
