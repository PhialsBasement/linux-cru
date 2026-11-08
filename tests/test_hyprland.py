import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from linux_cru import wayland


class _Res:
    def __init__(self, stdout="", rc=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, rc, stderr


MONITORS = '[{"name": "DP-1", "x": 1920, "y": 0, "scale": 1.25}]'


def _fake(replies):
    calls = []

    def run(cmd):
        calls.append(cmd)
        if cmd[:2] == ["hyprctl", "monitors"]:
            return _Res(MONITORS)
        return replies.pop(0)
    return run, calls


def test_legacy_keyword_keeps_position_and_scale(monkeypatch):
    run, calls = _fake([_Res("ok")])
    monkeypatch.setattr(wayland, "_run", run)
    ok, _ = wayland.hyprland_apply_modeline("DP-1", "148.50 1920 2008 2052 2200 1080 1084 1089 1125 +hsync +vsync")
    assert ok
    assert calls[-1] == ["hyprctl", "keyword", "monitor",
                         "DP-1, modeline 148.50 1920 2008 2052 2200 1080 1084 1089 1125 +hsync +vsync, 1920x0, 1.25"]


def test_lua_config_falls_back_to_eval(monkeypatch):
    # Hyprland 0.55+ with hyprland.lua: exit 0, error on stdout
    run, calls = _fake([_Res("keyword can't work with non-legacy parsers. Use eval."),
                        _Res("ok")])
    monkeypatch.setattr(wayland, "_run", run)
    ok, msg = wayland.hyprland_set_mode("DP-1", 1280, 1024, 75)
    assert ok, msg
    assert calls[-1][:2] == ["hyprctl", "eval"]
    assert calls[-1][2] == ('hl.monitor({ output = "DP-1", mode = "1280x1024@75", '
                            'position = "1920x0", scale = 1.25 })')


def test_rejected_keyword_is_not_success(monkeypatch):
    run, _ = _fake([_Res("keyword can't work with non-legacy parsers. Use eval."),
                    _Res("[string]:1: bad mode")])
    monkeypatch.setattr(wayland, "_run", run)
    ok, msg = wayland.hyprland_set_mode("DP-1", 1280, 1024, 75)
    assert not ok and "bad mode" in msg
