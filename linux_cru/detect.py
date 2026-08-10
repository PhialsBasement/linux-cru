"""Environment detection: session type, compositor, GPU drivers, connectors,
and which mode-injection paths are available on this machine.

Everything here is best-effort and read-only; failures degrade to
"unknown" rather than raising.
"""

import glob
import os
import re
import shutil
import subprocess

from dataclasses import dataclass, field

from . import hostenv


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _run(cmd):
    try:
        return subprocess.check_output(
            cmd, universal_newlines=True, stderr=subprocess.DEVNULL, timeout=5,
            env=hostenv.subprocess_env(),
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _version_tuple(text):
    """First X.Y[.Z] version in text, as a tuple of ints; () if none."""
    if not text:
        return ()
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not m:
        return ()
    return tuple(int(g) for g in m.groups() if g is not None)


@dataclass
class Connector:
    card: str            # e.g. "card1"
    name: str            # DRM connector name, e.g. "DP-1"
    status: str          # connected / disconnected / unknown
    enabled: str
    has_edid: bool
    modes: list = field(default_factory=list)

    @property
    def sysfs_path(self):
        return f"/sys/class/drm/{self.card}-{self.name}"


@dataclass
class GPU:
    card: str            # e.g. "card1"
    driver: str          # amdgpu / i915 / xe / nouveau / nvidia / ...


@dataclass
class Environment:
    session_type: str = "unknown"        # x11 / wayland / unknown
    desktop: str = ""                    # raw XDG_CURRENT_DESKTOP
    compositor: str = "unknown"          # mutter / kwin / sway / hyprland / cosmic / ...
    compositor_version: tuple = ()
    gpus: list = field(default_factory=list)
    connectors: list = field(default_factory=list)
    kernel_release: str = ""
    kernel_version: tuple = ()
    lockdown: str = "unknown"            # none / integrity / confidentiality / unknown
    initramfs_tool: str = ""             # mkinitcpio / dracut / update-initramfs / ""
    bootloader: str = "unknown"          # grub / systemd-boot / unknown
    nvidia_version: tuple = ()
    nvidia_drm_modeset: bool = False

    # -- convenience -------------------------------------------------------

    @property
    def drivers(self):
        return sorted({g.driver for g in self.gpus})

    @property
    def has_nvidia_proprietary(self):
        return "nvidia" in self.drivers

    def connected_connectors(self):
        return [c for c in self.connectors if c.status == "connected"]

    @property
    def is_wlroots_family(self):
        return self.compositor in ("sway", "hyprland", "labwc", "river", "niri", "wayfire")

    @property
    def kde_custom_modes_available(self):
        """kscreen-doctor addCustomMode landed in Plasma 6.6."""
        return self.compositor == "kwin" and self.compositor_version >= (6, 6)

    @property
    def nvidia_edid_override_ok(self):
        """drm.edid_firmware honored by the NVIDIA driver: >=535 + kernel >=6.2."""
        return (self.nvidia_version >= (535,)
                and self.kernel_version >= (6, 2)
                and self.nvidia_drm_modeset)


def detect() -> Environment:
    env = Environment()
    env.session_type = _session_type()
    env.desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    env.compositor, env.compositor_version = _compositor(env)
    env.gpus = _gpus()
    env.connectors = _connectors()
    env.kernel_release = os.uname().release
    env.kernel_version = _version_tuple(env.kernel_release)
    env.lockdown = _lockdown()
    env.initramfs_tool = _initramfs_tool()
    env.bootloader = _bootloader()
    env.nvidia_version = _version_tuple(_read("/sys/module/nvidia/version"))
    env.nvidia_drm_modeset = _read("/sys/module/nvidia_drm/parameters/modeset") == "Y"
    return env


def _session_type():
    st = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if st in ("x11", "wayland"):
        return st
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def _compositor(env):
    desktop = env.desktop.lower()
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or "hyprland" in desktop:
        return "hyprland", _version_tuple(_run(["hyprctl", "version"]))
    if os.environ.get("SWAYSOCK") or desktop == "sway":
        return "sway", _version_tuple(_run(["sway", "--version"]))
    if "kde" in desktop or "plasma" in desktop:
        return "kwin", _version_tuple(_run(["plasmashell", "--version"]))
    if "gnome" in desktop:
        return "mutter", _version_tuple(_run(["gnome-shell", "--version"]))
    if "cosmic" in desktop:
        return "cosmic", ()
    for name in ("river", "labwc", "niri", "wayfire"):
        if name in desktop:
            return name, ()
    if env.session_type == "x11":
        return "x11", ()
    return (desktop or "unknown"), ()


def _gpus():
    gpus = []
    for card_path in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
        card = os.path.basename(card_path)
        if "-" in card:  # connector entry, not a card
            continue
        driver_link = os.path.join(card_path, "device", "driver")
        try:
            driver = os.path.basename(os.readlink(driver_link))
        except OSError:
            continue
        gpus.append(GPU(card=card, driver=driver))
    return gpus


def _connectors():
    connectors = []
    for path in sorted(glob.glob("/sys/class/drm/card[0-9]*-*")):
        base = os.path.basename(path)
        card, _, name = base.partition("-")
        edid = None
        try:
            with open(os.path.join(path, "edid"), "rb") as f:
                edid = f.read()
        except OSError:
            pass
        modes = (_read(os.path.join(path, "modes")) or "").splitlines()
        connectors.append(Connector(
            card=card,
            name=name,
            status=_read(os.path.join(path, "status")) or "unknown",
            enabled=_read(os.path.join(path, "enabled")) or "unknown",
            has_edid=bool(edid),
            modes=modes,
        ))
    return connectors


def _lockdown():
    raw = _read("/sys/kernel/security/lockdown")
    if not raw:
        return "unknown"
    m = re.search(r"\[(\w+)\]", raw)
    return m.group(1) if m else "unknown"


def _initramfs_tool():
    for tool in ("mkinitcpio", "dracut", "update-initramfs"):
        if shutil.which(tool):
            return tool
    return ""


def _bootloader():
    if os.path.isdir("/boot/loader/entries"):
        return "systemd-boot"
    if os.path.exists("/etc/default/grub") or os.path.exists("/boot/grub/grub.cfg"):
        return "grub"
    return "unknown"


def compositor_can_apply(env: Environment, standard: str) -> bool:
    """Can the display server itself produce exactly these timings?

    Compositors differ in how much of a mode they accept. Some take a
    full modeline, some take only width/height/refresh and compute the
    timings with their own CVT implementation, and some cannot add
    modes at all. When the answer is no, the mode has to go in through
    an EDID override instead.
    """
    if env.session_type == "x11":
        return True                      # xrandr takes a full modeline
    if env.has_nvidia_proprietary:
        return False                     # the driver rejects them all
    if env.compositor in ("sway", "hyprland"):
        return True                      # both accept a full modeline
    if env.compositor == "kwin":
        # kscreen-doctor takes width/height/refresh plus a blanking
        # choice; KWin then computes CVT or CVT-RB with libxcvt.
        return env.kde_custom_modes_available and standard in ("cvt", "cvt-rb")
    if env.is_wlroots_family:
        # wlr-randr's custom mode is CVT with full blanking only.
        return standard == "cvt"
    return False                         # GNOME, COSMIC, anything unknown


def why_edid_needed(env: Environment, standard: str) -> str:
    """Short reason the compositor cannot apply these timings."""
    name = {"cvt": "CVT", "cvt-rb": "CVT-RB", "cvt-rb2": "CVT-RBv2"}.get(
        standard, standard)
    if env.has_nvidia_proprietary:
        return "the NVIDIA driver does not accept custom modes from Wayland " \
               "compositors"
    if env.compositor == "kwin":
        if not env.kde_custom_modes_available:
            return (f"KDE Plasma {_fmt_ver(env.compositor_version)} cannot add "
                    "custom modes (that needs Plasma 6.6 or newer)")
        return f"KWin cannot generate {name} timings"
    if env.is_wlroots_family:
        return f"{env.compositor} can only generate CVT timings with full blanking"
    if env.compositor == "mutter":
        return "GNOME cannot add custom modes"
    if env.compositor == "cosmic":
        return "custom modes are currently broken in COSMIC"
    return f"{env.compositor} cannot add custom modes"


# -- current mode of an output ----------------------------------------------

def current_mode(env: Environment, output: str):
    """(width, height, refresh) active on `output`, or None if unknown.

    Uses xrandr on X11 and the compositor's own CLI on Wayland. A mode
    is only returned if it is usable: virtual displays (Xvfb, some
    virtual machines) report a refresh rate of 0, and feeding that back
    into the interface leaves it unable to calculate anything.
    """
    mode = None
    try:
        if env.session_type == "x11":
            mode = _current_mode_xrandr(output)
        elif env.session_type == "wayland":
            if env.compositor == "kwin":
                mode = _current_mode_kscreen(output)
            elif env.compositor == "sway":
                mode = _current_mode_sway(output)
            elif env.compositor == "hyprland":
                mode = _current_mode_hyprland(output)
            elif env.is_wlroots_family:
                mode = _current_mode_wlr_randr(output)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None

    if not mode:
        return None
    width, height, refresh = mode
    if width <= 0 or height <= 0 or refresh <= 0:
        return None
    return mode


def _current_mode_xrandr(output):
    out = _run(["xrandr", "-q"])
    if not out:
        return None
    in_block = False
    for line in out.splitlines():
        if not line.startswith((" ", "\t")):
            tokens = line.split()
            in_block = bool(tokens) and tokens[0] == output
            continue
        if in_block and "*" in line:
            tokens = line.split()
            w, _, h = tokens[0].partition("x")
            for tok in tokens[1:]:
                if "*" in tok:
                    return int(w), int(h), float(tok.replace("*", "").replace("+", ""))
    return None


def _current_mode_kscreen(output):
    import json
    out = _run(["kscreen-doctor", "-j"])
    if not out:
        return None
    data = json.loads(out)
    for o in data.get("outputs", []):
        if o.get("name") != output:
            continue
        current = str(o.get("currentModeId"))
        for m in o.get("modes", []):
            if str(m.get("id")) == current:
                size = m.get("size", {})
                return (int(size.get("width")), int(size.get("height")),
                        float(m.get("refreshRate")))
    return None


def _current_mode_sway(output):
    import json
    out = _run(["swaymsg", "-t", "get_outputs"])
    if not out:
        return None
    for o in json.loads(out):
        if o.get("name") == output and o.get("current_mode"):
            m = o["current_mode"]
            return int(m["width"]), int(m["height"]), m["refresh"] / 1000.0
    return None


def _current_mode_hyprland(output):
    import json
    out = _run(["hyprctl", "monitors", "-j"])
    if not out:
        return None
    for o in json.loads(out):
        if o.get("name") == output:
            return int(o["width"]), int(o["height"]), float(o["refreshRate"])
    return None


def _current_mode_wlr_randr(output):
    out = _run(["wlr-randr"])
    if not out:
        return None
    in_block = False
    for line in out.splitlines():
        if not line.startswith((" ", "\t")):
            in_block = line.split()[0] == output if line.split() else False
            continue
        if in_block and "current" in line:
            m = re.search(r"(\d+)x(\d+)\s+px,\s+([\d.]+)\s+Hz", line)
            if m:
                return int(m.group(1)), int(m.group(2)), float(m.group(3))
    return None


# -- human-readable capability summary --------------------------------------

def describe_paths(env: Environment) -> str:
    """One-paragraph summary of which mode-injection paths work here."""
    drv = "+".join(env.drivers) or "unknown GPU"

    if env.session_type == "x11":
        if env.has_nvidia_proprietary:
            return ("Custom modes on the NVIDIA driver need ModeValidation in xorg.conf. "
                    "Click Apply Configuration, then restart X. After that Test Mode works.")
        return ("Test Mode applies the mode immediately and reverts automatically. "
                "Apply Configuration saves it to /etc/X11/xorg.conf.d.")

    if env.session_type == "wayland":
        if env.has_nvidia_proprietary:
            if env.nvidia_edid_override_ok:
                return ("The NVIDIA driver does not accept custom modes from Wayland "
                        "compositors. An EDID override is the only way, and your setup "
                        "supports it. Warning: EDID overrides currently break VRR on NVIDIA.")
            return ("The NVIDIA driver does not accept custom modes from Wayland "
                    "compositors. An EDID override needs driver 535 or newer, kernel 6.2 "
                    f"or newer, and nvidia-drm.modeset=1. Your setup: driver "
                    f"{_fmt_ver(env.nvidia_version)}, kernel {env.kernel_release}, "
                    f"modeset {'on' if env.nvidia_drm_modeset else 'off'}.")
        if env.compositor in ("sway", "hyprland"):
            return (f"{env.compositor} supports custom modelines. "
                    "See the commands in the preview.")
        if env.compositor == "kwin":
            if env.kde_custom_modes_available:
                return ("KDE Plasma supports custom modes through kscreen-doctor. "
                        "See the commands in the preview.")
            return (f"KDE Plasma {_fmt_ver(env.compositor_version)} cannot add custom "
                    "modes. That needs Plasma 6.6 or newer, or an EDID override.")
        if env.compositor == "mutter":
            return ("GNOME has no way to add custom modes. "
                    "An EDID override is the only option.")
        if env.compositor == "cosmic":
            return ("Custom modes are currently broken in COSMIC. "
                    "Use an EDID override instead.")
        if env.is_wlroots_family:
            return (f"{env.compositor} accepts simple custom modes through wlr-randr. "
                    "Exact timings need an EDID override.")
        return (f"Unknown compositor ({env.compositor}). "
                "An EDID override works everywhere.")

    return "Could not detect the session type."


def _fmt_ver(v):
    return ".".join(str(x) for x in v) if v else "unknown"
