"""Environment for running system tools.

When this runs from an AppImage, the process has PYTHONHOME,
PYTHONPATH and LD_LIBRARY_PATH pointing into the bundle. Handing that
environment to a system tool (xrandr, kscreen-doctor, systemctl,
pkexec) makes it load the bundle's libraries instead of its own, which
can break it in ways that are hard to diagnose.

subprocess_env() returns the environment a system tool should see: the
bundle's variables removed, and whatever the user had before the
AppImage started put back.
"""

import os

# AppRun stores the caller's values here before overwriting them.
_SAVED_PREFIX = "LINUX_CRU_SAVED_"
_BUNDLE_VARS = ("PYTHONHOME", "PYTHONPATH", "LD_LIBRARY_PATH",
                "TCL_LIBRARY", "TK_LIBRARY", "TKPATH")


def in_appimage(env=None):
    env = os.environ if env is None else env
    return bool(env.get("APPDIR") or env.get("APPIMAGE")
                or env.get(_SAVED_PREFIX + "MARKER"))


def subprocess_env(base=None):
    """A copy of the environment that is safe to give to system tools."""
    env = dict(base if base is not None else os.environ)
    if not in_appimage(env):
        return env

    for name in _BUNDLE_VARS:
        saved = env.pop(_SAVED_PREFIX + name, None)
        if saved:
            env[name] = saved
        else:
            env.pop(name, None)

    # The bundle's bin directory must not shadow the system's tools.
    appdir = env.get("APPDIR")
    if appdir:
        parts = [p for p in env.get("PATH", "").split(os.pathsep)
                 if p and not p.startswith(appdir)]
        env["PATH"] = os.pathsep.join(parts) or "/usr/bin:/bin"

    for key in list(env):
        if key.startswith(_SAVED_PREFIX):
            del env[key]
    return env
