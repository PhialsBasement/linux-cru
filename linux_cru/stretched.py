"""Stretched resolutions for games.

A lower resolution stretched across the whole panel -- 4:3 on a 16:9
screen -- is standard in competitive shooters, because it widens player
models. The distortion is the point.

The display cannot do this on Wayland. KWin only offers GPU scaling on
internal panels (drm_connector.cpp: "only generate common modes on
internal panels, where we can be certain they will work"), and the DRM
property that would do it is owned by the compositor while it runs, so
no amount of root helps.

gamescope does it instead: it runs the game in a nested compositor at
the resolution you ask for and scales the result to the whole screen,
distorting it when told to. That works on Wayland, on any GPU, without
root.
"""

import shutil

# Resolutions people actually use for this, by aspect.
COMMON = {
    "4:3": [(1280, 960), (1024, 768), (800, 600), (1440, 1080), (1600, 1200)],
    "5:4": [(1280, 1024)],
    "16:10": [(1680, 1050), (1920, 1200)],
}


def available():
    return shutil.which("gamescope") is not None


def install_hint():
    return ("gamescope is not installed. It is packaged as 'gamescope' on "
            "Arch, Fedora and Debian/Ubuntu.")


def aspect(width, height):
    from math import gcd
    d = gcd(width, height) or 1
    return f"{width // d}:{height // d}"


def launch_options(native_width, native_height, width, height, refresh,
                   stretch=True, extra=""):
    """Steam launch options that run a game stretched to the whole screen."""
    parts = [
        "gamescope",
        f"-W {native_width}", f"-H {native_height}",     # what the screen is
        f"-w {width}", f"-h {height}",                   # what the game renders
    ]
    if refresh:
        parts.append(f"-r {refresh:g}")
    parts.append("-f")
    parts.append("-S stretch" if stretch else "-S fit")
    parts.append("--force-grab-cursor")
    if extra:
        parts.append(extra)
    parts.append("-- %command%")
    return " ".join(parts)


def describe(native_width, native_height, width, height, refresh):
    """The block shown in the preview for a stretched resolution."""
    stretching = (width < native_width or height < native_height
                  or aspect(width, height) != aspect(native_width, native_height))
    if not stretching:
        return ""

    lines = [
        f"# {width}x{height} ({aspect(width, height)}) is smaller than this "
        f"screen's {native_width}x{native_height} "
        f"({aspect(native_width, native_height)}).",
        "#",
        "# The display server will not stretch it: KWin only scales internal",
        "# laptop panels, and on Wayland the compositor owns that setting, so",
        "# root cannot change it either. Setting this as a screen mode gives",
        "# you the picture in part of the panel, not stretched.",
        "#",
        "# For a game, run it stretched with gamescope instead. Steam launch",
        "# options:",
        "",
        launch_options(native_width, native_height, width, height, refresh),
        "",
    ]
    if not available():
        lines.append(f"# {install_hint()}")
    return "\n".join(lines) + "\n"
