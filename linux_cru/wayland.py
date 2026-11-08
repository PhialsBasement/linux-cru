"""Wayland compositor apply backends.

KDE (Plasma 6.6+): kscreen-doctor addCustomMode / mode / removeCustomMode.
sway / Hyprland: native modeline commands over their IPC tools.

All functions are best-effort and return (ok, message) or data/None;
they never raise on tool failure.
"""

import json
import re
import subprocess
import time

from . import hostenv

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, universal_newlines=True,
                              timeout=10, env=hostenv.subprocess_env())
    except (OSError, subprocess.SubprocessError) as e:
        class _Failed:
            returncode = 1
            stdout = ""
            stderr = str(e)
        return _Failed()


# -- switching to a mode, whatever is running --------------------------------

def kernel_has_mode(card, connector, width, height):
    """True once the kernel lists the resolution for this connector.

    The compositor cannot switch to a mode it has not seen yet, and an
    EDID override only takes effect on the next detect, so this is the
    gate to wait on before trying.
    """
    try:
        with open(f"/sys/class/drm/{card}-{connector}/modes") as f:
            return f"{width}x{height}" in f.read().split()
    except OSError:
        return False


def wait_for_mode(card, connector, width, height, timeout=6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if kernel_has_mode(card, connector, width, height):
            return True
        time.sleep(0.25)
    return False


def set_mode(env, output, width, height, refresh):
    """Switch `output` to this mode. Returns (ok, message)."""
    session = env.session_type
    compositor = env.compositor

    if session == "x11":
        res = _run(["xrandr", "--output", output, "--mode",
                    f"{width}x{height}", "--rate", f"{refresh:g}"])
        return res.returncode == 0, (res.stderr or res.stdout)

    if compositor == "kwin":
        return kwin_set_mode(output, f"{width}x{height}@{refresh:g}")

    if compositor == "sway":
        return sway_set_mode(output, width, height, refresh)

    if compositor == "hyprland":
        return hyprland_set_mode(output, width, height, refresh)

    if compositor == "mutter":
        return gnome_set_mode(env, output, width, height, refresh)

    if env.is_wlroots_family:
        res = _run(["wlr-randr", "--output", output, "--mode",
                    f"{width}x{height}@{refresh:g}Hz"])
        return res.returncode == 0, (res.stderr or res.stdout)

    return False, f"no way to change the mode on {compositor}"


def gnome_set_mode(env, output, width, height, refresh):
    """GNOME, through gdctl (shipped with Mutter since GNOME 48).

    gdctl replaces the whole monitor layout, so with more than one
    display connected this would rearrange the others. Refuse rather
    than wreck someone's desk setup.
    """
    connected = [c for c in env.connectors if c.status == "connected"]
    if len(connected) > 1:
        return False, ("gdctl rewrites the whole monitor layout, so this tool "
                       "will not switch modes automatically with more than one "
                       "display connected. The mode has been added -- select it "
                       "in Settings > Displays.")
    res = _run(["gdctl", "set", "--logical-monitor", "--primary",
                "--monitor", output, "--mode",
                f"{width}x{height}@{refresh:.3f}"])
    if res.returncode == 0:
        return True, ""
    # Older GNOME has no gdctl.
    if "not found" in (res.stderr or "").lower():
        return False, ("gdctl is not available (it ships with GNOME 48 and "
                       "later). The mode has been added -- select it in "
                       "Settings > Displays.")
    return False, (res.stderr or res.stdout)


# -- KDE / kscreen-doctor ----------------------------------------------------

def kwin_state(output):
    """(current_mode_id, set_of_all_mode_ids) for `output`, or None."""
    res = _run(["kscreen-doctor", "-j"])
    if res.returncode != 0:
        return None
    try:
        data = json.loads(res.stdout)
    except ValueError:
        return None
    for o in data.get("outputs", []):
        if o.get("name") == output:
            ids = {str(m.get("id")) for m in o.get("modes", [])}
            return str(o.get("currentModeId")), ids
    return None


def kwin_add_custom_mode(output, width, height, refresh, reduced=True):
    """Add a custom mode. Returns (new_mode_id, "") or (None, error)."""
    state = kwin_state(output)
    if not state:
        return None, "could not read the display configuration"
    _, before = state

    mhz = int(round(refresh * 1000))
    blanking = "reduced" if reduced else "full"
    res = _run(["kscreen-doctor",
                f"output.{output}.addCustomMode.{width}.{height}.{mhz}.{blanking}"])
    if res.returncode != 0:
        return None, res.stderr or res.stdout or "kscreen-doctor failed"

    state = kwin_state(output)
    if not state:
        return None, "could not re-read the display configuration"
    _, after = state
    new = after - before
    if len(new) == 1:
        return new.pop(), ""
    return None, ("the compositor did not add a new mode "
                  "(an identical mode may already exist)")


def kwin_set_mode(output, mode_id):
    res = _run(["kscreen-doctor", f"output.{output}.mode.{mode_id}"])
    return res.returncode == 0, (res.stderr or res.stdout)


def kwin_remove_custom_mode(output, width, height):
    """Remove the first custom mode matching width x height. True on success."""
    res = _run(["kscreen-doctor", "-o"])
    if res.returncode != 0:
        return False
    text = _ANSI.sub("", res.stdout)
    in_output = False
    in_custom = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("Output:"):
            parts = s.split()
            in_output = len(parts) > 2 and parts[2] == output
            in_custom = False
            continue
        if in_output and s.startswith("Custom modes"):
            in_custom = "None" not in s
            continue
        if in_output and in_custom:
            m = re.match(r"(\d+):\s*(\d+)x(\d+)@", s)
            if not m:
                in_custom = False
                continue
            if int(m.group(2)) == width and int(m.group(3)) == height:
                res = _run(["kscreen-doctor",
                            f"output.{output}.removeCustomMode.{m.group(1)}"])
                return res.returncode == 0
    return False


# -- sway ---------------------------------------------------------------------

def sway_apply_modeline(output, timing_string):
    res = _run(["swaymsg", f"output {output} modeline {timing_string}"])
    return res.returncode == 0, (res.stderr or res.stdout)


def sway_set_mode(output, width, height, refresh):
    res = _run(["swaymsg", f"output {output} mode {width}x{height}@{refresh:g}Hz"])
    return res.returncode == 0, (res.stderr or res.stdout)


# -- Hyprland -------------------------------------------------------------------
#
# Hyprland 0.55+ can be configured in Lua (hyprland.lua). A session started
# from a Lua config rejects every `hyprctl keyword` -- on stdout, with exit
# status 0 -- and wants `hyprctl eval 'hl.monitor({...})'` instead. Try the
# legacy form first and fall back when the reply says so.

def hyprland_lua_config(home=None):
    """True if the user's Hyprland config is the Lua one."""
    import os
    home = home or os.path.expanduser("~")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return os.path.exists(os.path.join(base, "hypr", "hyprland.lua"))


def hyprland_layout(output):
    """(position, scale) of `output` so a mode change does not move it."""
    res = _run(["hyprctl", "monitors", "-j"])
    if res.returncode == 0:
        try:
            for o in json.loads(res.stdout):
                if o.get("name") == output:
                    return (f"{int(o.get('x', 0))}x{int(o.get('y', 0))}",
                            float(o.get("scale", 1)))
        except (ValueError, TypeError):
            pass
    return "auto", 1.0


def _lua_str(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def hyprland_lua_monitor(output, mode, position="auto", scale=1.0):
    """The hl.monitor({...}) call for a Lua config / hyprctl eval."""
    return (f"hl.monitor({{ output = {_lua_str(output)}, mode = {_lua_str(mode)}, "
            f"position = {_lua_str(position)}, scale = {scale:g} }})")


def _hyprland_rejects_keyword(text):
    t = (text or "").lower()
    return "non-legacy" in t or ("keyword" in t and "eval" in t)


def _hyprland_ok(res):
    return res.returncode == 0 and (res.stdout or "").strip().lower() == "ok"


def hyprland_monitor(output, mode):
    """Apply a monitor rule with `mode`, keeping position and scale."""
    position, scale = hyprland_layout(output)
    res = _run(["hyprctl", "keyword", "monitor",
                f"{output}, {mode}, {position}, {scale:g}"])
    if _hyprland_ok(res):
        return True, ""
    if _hyprland_rejects_keyword(res.stdout) or _hyprland_rejects_keyword(res.stderr):
        res = _run(["hyprctl", "eval",
                    hyprland_lua_monitor(output, mode, position, scale)])
        if _hyprland_ok(res):
            return True, ""
    return False, (res.stderr or res.stdout)


def hyprland_apply_modeline(output, timing_string):
    return hyprland_monitor(output, f"modeline {timing_string}")


def hyprland_set_mode(output, width, height, refresh):
    return hyprland_monitor(output, f"{width}x{height}@{refresh:g}")
