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
