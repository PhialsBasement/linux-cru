import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from linux_cru import active, timings

STATE = """\
plane[31]: plane-0
	crtc=crtc-0
crtc[73]: crtc-0
	enable=1
	active=1
	self_refresh_active=0
	planes_changed=1
	mode_changed=0
	mode: "1920x1080": 60 148500 1920 2008 2052 2200 1080 1084 1089 1125 0x48 0x5
crtc[74]: crtc-1
	enable=1
	active=1
	mode: "": 60 {rb2clock} 2560 2568 2600 2640 1440 {vss} {vse} {vt} 0x0 0x9
crtc[75]: crtc-2
	enable=0
	active=0
	mode: "": 0 0 0 0 0 0 0 0 0 0 0x0 0x0
connector[96]: DP-1
	crtc=crtc-0
	self_refresh_aware=0
connector[97]: HDMI-A-1
	crtc=crtc-1
connector[98]: DP-2
	crtc=(null)
"""


def _state():
    rb2 = timings.cvt_rb2(2560, 1440, 59.94)
    return STATE.format(rb2clock=rb2.clock_khz, vss=rb2.vsync_start,
                        vse=rb2.vsync_end, vt=rb2.vtotal), rb2


def test_parse_maps_connectors_to_active_crtcs():
    text, rb2 = _state()
    modes = active.parse_state(text)
    assert set(modes) == {"DP-1", "HDMI-A-1"}
    ml, inter = modes["DP-1"]
    assert not inter
    assert (ml.clock_khz, ml.htotal, ml.vtotal) == (148500, 2200, 1125)
    assert ml.hsync_positive and ml.vsync_positive
    ml2, _ = modes["HDMI-A-1"]
    assert ml2.vtotal == rb2.vtotal
    assert ml2.hsync_positive and not ml2.vsync_positive  # flags 0x9


def test_identify_standard():
    for std in ("cvt", "cvt-rb", "cvt-rb2"):
        ml = timings.calc(1920, 1080, 75, std)
        assert active.identify_standard(ml) == std, std
    _, rb2 = _state()
    assert active.identify_standard(rb2) == "cvt-rb2"
    # CEA 1080p60 is not any CVT formula
    cea = timings.parse_modeline("148.5 1920 2008 2052 2200 1080 1084 1089 1125 +hsync +vsync")
    assert active.identify_standard(cea) is None


def test_report_from_debugfs_tree():
    text, _ = _state()
    root = tempfile.mkdtemp()
    os.makedirs(f"{root}/0")
    with open(f"{root}/0/state", "w") as f:
        f.write(text)
    out = active.report(root)
    assert "DP-1 (dri/0): 1920x1080 @ 60.0000 Hz" in out, out
    assert "Modeline 148.50 1920 2008 2052 2200 1080 1084 1089 1125 +hsync +vsync" in out
    assert "matches: CVT-RB2" in out, out
