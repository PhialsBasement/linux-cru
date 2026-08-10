"""Output scaling: making a mode smaller than the panel fill the screen.

A mode narrower than the display leaves the rest of the panel dark
unless something scales it up. Most drivers expose a "scaling mode"
connector property for this (None / Full / Center / Full aspect), and
the driver's own scaler does the work.

Nothing here is driver-specific by design: the DRM property is standard
and the driver name is only ever used to point libdrm at the right
device. NVIDIA's proprietary driver is the exception, since it does not
expose the property and scales through its own ViewPort options
instead.
"""

import re
import subprocess

from . import hostenv

PROPERTY = "scaling mode"
VALUES = ("None", "Full", "Center", "Full aspect")


def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, universal_newlines=True,
                              timeout=10, env=hostenv.subprocess_env())
    except (OSError, subprocess.SubprocessError):
        class _Failed:
            returncode = 1
            stdout = ""
            stderr = ""
        return _Failed()


def _drm_driver(card):
    """Kernel driver behind a card, e.g. amdgpu / i915 / xe / nouveau."""
    import os
    try:
        return os.path.basename(
            os.readlink(f"/sys/class/drm/{card}/device/driver"))
    except OSError:
        return ""


def read_scaling_mode(card, connector):
    """Current value of the scaling property, or None if there isn't one.

    Reads through libdrm (modetest), pointed at whichever driver owns
    the card. Works the same on amdgpu, i915, xe and nouveau; NVIDIA's
    driver does not offer the property.
    """
    driver = _drm_driver(card)
    if not driver:
        return None
    res = _run(["modetest", "-M", driver, "-c"])
    if res.returncode != 0 or not res.stdout:
        return None
    return _parse_scaling_mode(res.stdout, connector)


def _parse_scaling_mode(text, connector):
    """Pull the connector's scaling value out of modetest output."""
    lines = text.splitlines()
    in_connector = False
    for i, line in enumerate(lines):
        # Connector headers look like: "438 437 connected DP-1 700x390 43 437"
        fields = line.split()
        if len(fields) >= 4 and fields[0].isdigit() and fields[1].isdigit():
            in_connector = fields[3] == connector
            continue
        if not in_connector:
            continue
        if re.match(rf"\s*\d+\s+{re.escape(PROPERTY)}:", line, re.I):
            for follow in lines[i + 1:i + 6]:
                m = re.match(r"\s*value:\s*(\d+)", follow)
                if m:
                    index = int(m.group(1))
                    return VALUES[index] if index < len(VALUES) else str(index)
            return None
    return None


def supported(env, card, connector):
    """(can_set, explanation)."""
    if env.has_nvidia_proprietary:
        return False, ("the NVIDIA driver does not offer the scaling property; "
                       "it scales with ViewPortIn/ViewPortOut in xorg.conf or "
                       "nvidia-settings instead")
    if read_scaling_mode(card, connector) is None:
        return False, ("this output has no scaling property, so only the "
                       "display's own scaler can fill the screen")
    if env.session_type == "x11":
        return True, ""
    return False, ("on Wayland the compositor owns this property, so it has to "
                   "be changed there rather than from this tool")


def set_scaling_mode(env, output, value="Full aspect"):
    """Set the property. Only possible on X11, where we can talk to RandR."""
    if env.session_type != "x11":
        return False, ("only possible on X11; on Wayland the compositor is the "
                       "DRM master and owns this property")
    res = _run(["xrandr", "--output", output, "--set", PROPERTY, value])
    return res.returncode == 0, (res.stderr or res.stdout)


def property_id(card, connector):
    """(connector_id, property_id) for the scaling property, or None."""
    driver = _drm_driver(card)
    if not driver:
        return None
    res = _run(["modetest", "-M", driver, "-c"])
    if res.returncode != 0:
        return None
    lines = res.stdout.splitlines()
    conn_id = None
    for i, line in enumerate(lines):
        fields = line.split()
        if len(fields) >= 4 and fields[0].isdigit() and fields[1].isdigit():
            conn_id = fields[0] if fields[3] == connector else None
            continue
        if conn_id:
            m = re.match(rf"\s*(\d+)\s+{re.escape(PROPERTY)}:", line, re.I)
            if m:
                return conn_id, m.group(1)
    return None


def fill_screen(env, output, card, connector, stretch=True):
    """Make the output scale up to the panel. Returns (ok, message).

    On X11 this takes effect at once. On Wayland the compositor holds
    DRM master and a change from here is ignored, so it is applied at
    boot instead, before the display manager starts.
    """
    value = "Full" if stretch else "Full aspect"
    if env.session_type == "x11":
        res = _run(["xrandr", "--output", output, "--set", PROPERTY, value])
        if res.returncode == 0:
            return True, f"scaling set to {value}"
        return False, (res.stderr or res.stdout)
    return False, "applied at boot (the compositor owns this while running)"


def boot_snippet(card, connector, stretch=True):
    """Shell that sets the scaler while we still hold DRM master."""
    ids = property_id(card, connector)
    if not ids:
        return ""
    conn_id, prop_id = ids
    value = 1 if stretch else 3
    driver = _drm_driver(card)
    return f"""
# Scale the output up to the panel. Only possible before the display
# manager starts, because it takes DRM master and keeps it.
if command -v proptest >/dev/null 2>&1; then
    proptest -M {driver} {conn_id} connector {prop_id} {value} >/dev/null 2>&1 || true
fi
"""
