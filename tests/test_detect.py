"""Smoke tests for the detection layer (read-only, environment-dependent)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from linux_cru import detect


def test_detect_runs_and_is_coherent():
    env = detect.detect()
    assert env.session_type in ("x11", "wayland", "unknown")
    assert isinstance(env.gpus, list)
    assert isinstance(env.connectors, list)
    for c in env.connectors:
        assert c.card.startswith("card")
        assert c.name and "-" in c.name or c.name  # DP-1, HDMI-A-1, eDP-1...
        assert c.status in ("connected", "disconnected", "unknown")
    assert isinstance(env.kernel_version, tuple)
    # describe_paths must always return something displayable
    text = detect.describe_paths(env)
    assert isinstance(text, str) and len(text) > 20


def test_xwayland_is_not_mistaken_for_an_x11_session():
    """Under XWayland the X11 route is a dead end, so it must not be chosen.

    A launcher can drop WAYLAND_DISPLAY, and XWayland then looks exactly
    like a plain X11 session: DISPLAY is set and xrandr even reports the
    real connector names. Applying anything through xrandr or xorg.conf
    in that state does nothing.
    """
    import os
    saved = dict(os.environ)
    try:
        for var in ("WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP"):
            os.environ.pop(var, None)
        os.environ["DISPLAY"] = ":0"

        detect._wayland_socket_present = lambda: True
        detect._running_compositor = lambda: "kwin"
        assert detect._session_type() == "wayland", \
            "a compositor is serving Wayland, so this is a Wayland session"

        # Even when the environment actively claims X11.
        os.environ["XDG_SESSION_TYPE"] = "x11"
        assert detect._session_type() == "wayland"

        # With no compositor, an X11 session is still an X11 session.
        detect._wayland_socket_present = lambda: False
        detect._running_compositor = lambda: ""
        assert detect._session_type() == "x11"

        # Nothing at all to go on.
        os.environ.pop("XDG_SESSION_TYPE")
        os.environ.pop("DISPLAY")
        assert detect._session_type() == "unknown"
    finally:
        os.environ.clear()
        os.environ.update(saved)
        importlib = __import__("importlib")
        importlib.reload(detect)


def test_compositor_identified_without_desktop_variable():
    """XDG_CURRENT_DESKTOP is often absent when a launcher scrubs the env."""
    import os
    saved = dict(os.environ)
    try:
        os.environ.pop("XDG_CURRENT_DESKTOP", None)
        detect._running_compositor = lambda: "kwin"
        detect._compositor_version = lambda name: (6, 7, 3)
        env = detect.Environment(session_type="wayland")
        name, version = detect._compositor(env)
        assert name == "kwin" and version == (6, 7, 3), (name, version)
    finally:
        os.environ.clear()
        os.environ.update(saved)
        __import__("importlib").reload(detect)


def test_unusable_modes_are_rejected():
    """Virtual displays report 0 Hz; accepting that breaks every calculation."""
    env = detect.Environment(session_type="x11", compositor="x11")
    xvfb_output = (
        "Screen 0: minimum 1 x 1, current 1280 x 1024, maximum 8192 x 8192\n"
        "default connected 1280x1024+0+0 0mm x 0mm\n"
        "   1280x1024      0.00*\n")
    real = detect._run
    detect._run = lambda cmd: xvfb_output
    try:
        assert detect.current_mode(env, "default") is None, \
            "a 0 Hz mode must not be reported as usable"
    finally:
        detect._run = real

    good = ("Screen 0: minimum 320 x 200\n"
            "DP-1 connected primary 2560x1440+0+0 597mm x 336mm\n"
            "   2560x1440    143.91*+  120.00\n")
    detect._run = lambda cmd: good
    try:
        assert detect.current_mode(env, "DP-1") == (2560, 1440, 143.91)
    finally:
        detect._run = real


def test_version_tuple():
    assert detect._version_tuple("plasmashell 6.6.1") == (6, 6, 1)
    assert detect._version_tuple("Hyprland 0.53.2 built from...") == (0, 53, 2)
    assert detect._version_tuple("6.14.3-2-cachyos") == (6, 14, 3)
    assert detect._version_tuple(None) == ()
    assert detect._version_tuple("no digits") == ()
    assert (6, 6) >= (6, 6)
    assert detect._version_tuple("6.5.90") < (6, 6)


if __name__ == "__main__":
    test_detect_runs_and_is_coherent()
    test_version_tuple()
    print("detect tests passed")
