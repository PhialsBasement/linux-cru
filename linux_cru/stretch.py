"""Stretched resolutions on KWin Wayland.

The main reason to run a non-native resolution in a competitive shooter
is that it is stretched across the whole panel, which widens player
models. KWin refuses to do this on external monitors: it only turns on
the GPU scaler for internal laptop panels (the isInternal() check in
drm_pipeline.cpp's prepareAtomicModeset), and while it runs it owns the
DRM scaling property, so nothing external can set it.

But that same function checks the KWIN_DRM_FORCE_SCALING_MODE
environment variable *before* the internal-panel test, and the force
path applies to every connector. So there are two honest ways to make
external stretching work:

- "login": write the env var into the Plasma login environment. Takes
  effect after logging out and back in, and stays until removed. This
  is the clean, supported mechanism.

- "live": patch the running KWin. The env var is read once into a
  static std::optional at startup; a restart would be needed to pick it
  up. Instead we find that static in the process's memory (by the byte
  signature of the code that reads it) and flip it to "Full", so the
  next resolution change stretches. No restart, but it lasts only until
  KWin exits.

FULL stretches to fill (what a 4:3 stretched res needs). FULL_ASPECT
would keep the aspect ratio and pillarbox instead.
"""

import os
import re
import struct

ENV_VAR = "KWIN_DRM_FORCE_SCALING_MODE"
# systemd user environment.d: read by the systemd user session and so
# inherited by KWin, which runs as the plasma-kwin_wayland.service user
# unit. This is the robust persistence -- it survives reboots and does
# not depend on a login shell sourcing anything.
ENVIRONMENT_D_FILE = os.path.expanduser(
    "~/.config/environment.d/linux-cru-stretch.conf")

VALUES = {"none": 0, "full": 1, "center": 2, "full_aspect": 3}

# The read site in DrmPipeline::prepareAtomicModeset compiles to:
#   cmpb $0x0, [rip+engaged]     80 3D ?? ?? ?? ?? 00     s_forceScalingMode.has_value()
#   je   <else branch>           0F 84 ?? ?? ?? ??
#   mov  r8, [rip+value]         4C 8B 05 ?? ?? ?? ??     *s_forceScalingMode
# The two rip-relative displacements give the addresses of the optional's
# engaged byte and its value. Matching by pattern rather than a fixed
# offset means the patch keeps working when KWin is rebuilt, and simply
# does not match (rather than corrupting anything) if the code changes.
_SIGNATURE = re.compile(
    rb"\x80\x3d(....)\x00"      # cmpb $0, [rip+disp32]   -> engaged
    rb".{0,24}?"               # scheduled insns + the je in between
    rb"\x4c\x8b\x05(....)",     # mov r8, [rip+disp32]    -> value
    re.DOTALL)


# -- the supported, persistent method (systemd environment.d) ------------------

def persistent_value():
    """The scaling value persisted for future sessions, or None."""
    try:
        with open(ENVIRONMENT_D_FILE) as f:
            m = re.search(rf"{ENV_VAR}=(\w+)", f.read())
            return m.group(1) if m else None
    except OSError:
        return None


def write_persistent(value="FULL"):
    """Persist the env var via systemd environment.d so KWin picks it up
    at the next login and every one after."""
    os.makedirs(os.path.dirname(ENVIRONMENT_D_FILE), exist_ok=True)
    with open(ENVIRONMENT_D_FILE, "w") as f:
        f.write("# Managed by Linux CRU. Read by the systemd user session and\n"
                "# inherited by KWin, so a resolution smaller than the panel is\n"
                "# stretched to fill it, on external monitors too. Delete this\n"
                "# file to undo.\n"
                f"{ENV_VAR}={value}\n")


def remove_persistent():
    try:
        os.remove(ENVIRONMENT_D_FILE)
        return True
    except OSError:
        return False


# -- the live, no-restart method -----------------------------------------------

class LivePatchError(RuntimeError):
    pass


def kwin_pid():
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/comm") as f:
                if f.read().strip() == "kwin_wayland":
                    return int(entry)
        except OSError:
            continue
    return None


def _libkwin_mapping(pid):
    """(on-disk path, load base) for libkwin in the process, or None."""
    base = None
    path = None
    with open(f"/proc/{pid}/maps") as f:
        for line in f:
            if "libkwin.so" not in line:
                continue
            start = int(line.split("-")[0], 16)
            base = start if base is None else min(base, start)
            # maps line: addr perms offset dev inode pathname; the pathname
            # is field 5 and may carry a trailing " (deleted)" after an update
            fields = line.split(maxsplit=5)
            if len(fields) == 6:
                path = fields[5].strip()
                if path.endswith("(deleted)"):
                    path = path[:-len("(deleted)")].strip()
    if base is None:
        return None
    return path, base


def _find_static(code):
    """Locate the optional's engaged and value virtual addresses.

    `code` is the whole library file. Returns (engaged_vaddr, value_vaddr)
    or None. rip-relative addresses are resolved against each
    instruction's own file offset, which equals its vaddr for libkwin
    (the loadable segments are mapped at file offset == vaddr). The
    optional's engaged byte sits 8 bytes above its value; a match whose
    two references are not 8 apart is a coincidence and is rejected.
    """
    for m in _SIGNATURE.finditer(code):
        engaged = (m.start() + 7) + struct.unpack("<i", m.group(1))[0]
        mov_off = m.start(2) - 3
        value = (mov_off + 7) + struct.unpack("<i", m.group(2))[0]
        if engaged - value == 8:
            return engaged, value
    return None


def live_patch_available():
    """(ok, reason). Whether the live poke can run here."""
    if os.geteuid() != 0 and not _can_use_sudo():
        return False, "needs root to write another process's memory"
    if kwin_pid() is None:
        return False, "kwin_wayland is not running"
    return True, ""


def _can_use_sudo():
    # The GUI runs the poke through the privileged helper, which handles
    # auth; this is only a soft check for messaging.
    import shutil
    return bool(shutil.which("sudo") or shutil.which("pkexec"))


def build_live_patch_script(value="full"):
    """A root script that flips the running KWin's scaling static.

    HACK!!: Replace as soon as KWin gets off their high horse about
    non-laptop panels scaling on Wayland. This pokes the running
    compositor's memory to force the DRM scaling mode because KWin gates
    the scaler on isInternal(); if upstream ever sets it for external
    outputs (or honours a config option), delete this whole path.

    It re-derives the addresses from the byte signature in the process's
    own memory every run, so it needs no on-disk library and does
    nothing if the signature is gone -- it cannot corrupt a KWin it does
    not recognise.
    """
    enum = VALUES.get(value.lower(), 1)
    return f'''#!/usr/bin/env python3
# Force KWin's DRM scaling mode by patching the running compositor, so a
# resolution smaller than the panel is stretched to fill it. Reads the
# code straight from the process's memory and locates the target by byte
# signature, so it needs no on-disk library and works across KWin
# rebuilds; if the signature is gone it does nothing rather than corrupt.
import os, re, struct, sys

ENUM = {enum}
SIG = re.compile(
    rb"\\x80\\x3d(....)\\x00.{{0,24}}?\\x4c\\x8b\\x05(....)", re.DOTALL)

def kwin_pid():
    for e in os.listdir("/proc"):
        if e.isdigit():
            try:
                if open(f"/proc/{{e}}/comm").read().strip() == "kwin_wayland":
                    return int(e)
            except OSError:
                pass
    return None

pid = kwin_pid()
if not pid:
    print("kwin_wayland not running", file=sys.stderr); sys.exit(1)

# the executable mapping of libkwin holds the code we scan
seg = None
for line in open(f"/proc/{{pid}}/maps"):
    if "libkwin.so" in line and "r-xp" in line:
        a, b = line.split()[0].split("-")
        seg = (int(a, 16), int(b, 16)); break
if not seg:
    print("libkwin code segment not mapped", file=sys.stderr); sys.exit(1)

start, end = seg
with open(f"/proc/{{pid}}/mem", "rb") as mem:
    mem.seek(start); code = mem.read(end - start)

eng = val = None
for m in SIG.finditer(code):
    e = (start + m.start() + 7) + struct.unpack("<i", m.group(1))[0]
    v = (start + m.start(2) - 3 + 7) + struct.unpack("<i", m.group(2))[0]
    if e - v == 8:              # the optional's engaged byte sits at value+8
        eng, val = e, v; break
if eng is None:
    print("scaling signature not found -- this KWin version is not "
          "supported (or no longer needs the patch)", file=sys.stderr)
    sys.exit(2)

with open(f"/proc/{{pid}}/mem", "r+b") as mem:
    mem.seek(val); mem.write(bytes([ENUM]))
    mem.seek(eng); mem.write(b"\\x01" if ENUM else b"\\x00")
print(f"patched: scaling forced to enum {{ENUM}} "
      f"(0=off 1=full 2=center 3=full-aspect)")
'''


def build_live_unpatch_script():
    """A root script that clears the forced scaling (back to nullopt)."""
    return build_live_patch_script("none")
