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


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _run(cmd):
    try:
        return subprocess.check_output(
            cmd, universal_newlines=True, stderr=subprocess.DEVNULL, timeout=5
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


# -- human-readable capability summary --------------------------------------

def describe_paths(env: Environment) -> str:
    """One-paragraph summary of which mode-injection paths work here."""
    drv = "+".join(env.drivers) or "unknown GPU"

    if env.session_type == "x11":
        if env.has_nvidia_proprietary:
            return (f"X11 + NVIDIA ({_fmt_ver(env.nvidia_version)}): custom modelines need "
                    "xorg.conf ModeValidation (use Apply, then restart X). After that, "
                    "Test Mode works live via xrandr.")
        return (f"X11 + {drv}: Test Mode applies live via xrandr with auto-revert; "
                "Apply persists via /etc/X11/xorg.conf.d.")

    if env.session_type == "wayland":
        if env.has_nvidia_proprietary:
            if env.nvidia_edid_override_ok:
                return ("Wayland + NVIDIA: compositor custom modes are rejected by the "
                        "driver — the only working path is a kernel EDID override "
                        "(supported on your setup: driver ≥ 535, kernel ≥ 6.2, modeset on). "
                        "Note: EDID override currently disables VRR/G-SYNC (NVIDIA bug).")
            return ("Wayland + NVIDIA: EDID override requires driver ≥ 535, kernel ≥ 6.2 "
                    f"and nvidia-drm.modeset=1 — your setup: driver {_fmt_ver(env.nvidia_version)}, "
                    f"kernel {env.kernel_release}, modeset={'on' if env.nvidia_drm_modeset else 'off'}.")
        if env.compositor in ("sway", "hyprland"):
            return (f"Wayland + {drv} on {env.compositor}: full custom modelines supported "
                    "natively — see the generated commands in the preview.")
        if env.compositor == "kwin":
            if env.kde_custom_modes_available:
                return (f"Wayland + {drv} on KDE Plasma {_fmt_ver(env.compositor_version)}: "
                        "custom modes via kscreen-doctor addCustomMode — see preview.")
            return (f"Wayland + {drv} on KDE Plasma {_fmt_ver(env.compositor_version)}: "
                    "custom modes need Plasma ≥ 6.6 (or a kernel EDID override).")
        if env.compositor == "mutter":
            return (f"Wayland + {drv} on GNOME: GNOME offers no compositor-level custom "
                    "modes — the kernel EDID override is the only path.")
        if env.compositor == "cosmic":
            return (f"Wayland + {drv} on COSMIC: custom modes are currently broken in "
                    "cosmic-comp — use a kernel EDID override.")
        if env.is_wlroots_family:
            return (f"Wayland + {drv} on {env.compositor}: wlr-randr --custom-mode works "
                    "(CVT timings computed by the compositor); full modelines need an "
                    "EDID override.")
        return (f"Wayland + {drv} on {env.compositor}: unknown compositor — the kernel "
                "EDID override is the universal path.")

    return "Could not determine session type (no DISPLAY or WAYLAND_DISPLAY)."


def _fmt_ver(v):
    return ".".join(str(x) for x in v) if v else "unknown"
