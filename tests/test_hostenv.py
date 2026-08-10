"""Environment scrubbing for system tools launched from the AppImage."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from linux_cru import hostenv


BUNDLED = {
    "APPDIR": "/tmp/.mount_abc",
    "PATH": "/tmp/.mount_abc/usr/bin:/usr/bin:/bin",
    "PYTHONHOME": "/tmp/.mount_abc/usr",
    "PYTHONPATH": "/tmp/.mount_abc/usr/lib/python3.12",
    "LD_LIBRARY_PATH": "/tmp/.mount_abc/usr/lib",
    "TCL_LIBRARY": "/tmp/.mount_abc/usr/lib/tcl8.6",
    "TK_LIBRARY": "/tmp/.mount_abc/usr/lib/tk8.6",
    "HOME": "/home/user",
    "LINUX_CRU_SAVED_MARKER": "1",
}


def test_outside_appimage_is_unchanged():
    base = {"PATH": "/usr/bin", "LD_LIBRARY_PATH": "/opt/mine/lib"}
    assert hostenv.subprocess_env(base) == base


def test_bundle_variables_removed():
    env = hostenv.subprocess_env(BUNDLED)
    for name in ("PYTHONHOME", "PYTHONPATH", "LD_LIBRARY_PATH",
                 "TCL_LIBRARY", "TK_LIBRARY"):
        assert name not in env, f"{name} leaked into the child environment"
    assert env["HOME"] == "/home/user", "unrelated variables must survive"


def test_bundle_bin_removed_from_path():
    env = hostenv.subprocess_env(BUNDLED)
    assert "/tmp/.mount_abc" not in env["PATH"], env["PATH"]
    assert "/usr/bin" in env["PATH"] and "/bin" in env["PATH"], env["PATH"]


def test_users_own_values_are_restored():
    base = dict(BUNDLED)
    base["LINUX_CRU_SAVED_LD_LIBRARY_PATH"] = "/opt/user/lib"
    base["LINUX_CRU_SAVED_PYTHONPATH"] = "/home/user/pylibs"
    env = hostenv.subprocess_env(base)
    assert env["LD_LIBRARY_PATH"] == "/opt/user/lib"
    assert env["PYTHONPATH"] == "/home/user/pylibs"
    assert "PYTHONHOME" not in env, "nothing was saved for it, so it must go"


def test_saved_variables_do_not_leak():
    base = dict(BUNDLED, LINUX_CRU_SAVED_LD_LIBRARY_PATH="/opt/user/lib")
    env = hostenv.subprocess_env(base)
    assert not any(k.startswith("LINUX_CRU_SAVED_") for k in env), sorted(env)


def test_path_never_ends_up_empty():
    base = {"APPDIR": "/tmp/.mount_x", "PATH": "/tmp/.mount_x/usr/bin",
            "LINUX_CRU_SAVED_MARKER": "1"}
    env = hostenv.subprocess_env(base)
    assert env["PATH"], "a child with no PATH could not find any tool"


def test_detection():
    assert hostenv.in_appimage() is False or "APPDIR" in os.environ


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failures else 0)
