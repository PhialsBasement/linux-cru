"""Running commands as root.

Tries, in order: already being root, passwordless sudo, pkexec (the
graphical prompt a desktop normally provides), and finally asking for a
password and feeding it to sudo. The password prompt is supplied by the
caller so this module stays free of any toolkit.

Callers get back a Result that distinguishes "the user cancelled" from
"it failed", so cancelling an authentication dialog does not raise an
error at them.
"""

import os
import shutil
import subprocess

from . import hostenv

SUDO_ATTEMPTS = 3

# pkexec says this when there is no authentication agent to talk to,
# which is the case on minimal setups and plain TTYs.
_NO_AGENT_MARKERS = (
    "no authentication agent",
    "cannot open display",
    "polkit-agent-helper",
)
_DISMISSED_MARKERS = (
    "request dismissed",
    "not authorized",
    "authentication failed",
)


class Result:
    def __init__(self, ok, message="", cancelled=False):
        self.ok = ok
        self.message = message
        self.cancelled = cancelled

    def __bool__(self):
        return self.ok

    def __iter__(self):
        """Allows: ok, message = run_as_root(...)"""
        return iter((self.ok, self.message))


def _run(argv, stdin_text=None):
    try:
        p = subprocess.run(argv, capture_output=True, universal_newlines=True,
                           input=stdin_text, env=hostenv.subprocess_env())
        return p.returncode, p.stdout, p.stderr
    except OSError as e:
        return 127, "", str(e)


def have_root():
    return os.geteuid() == 0


def passwordless_sudo():
    """True when sudo runs without asking for anything."""
    if not shutil.which("sudo"):
        return False
    rc, _, _ = _run(["sudo", "-n", "true"])
    return rc == 0


def run_as_root(argv, ask_password=None, allow_pkexec=True):
    """Run argv as root.

    ask_password: callable(prompt, retry) -> str or None. Called only if
    the automatic routes are unavailable; returning None cancels.
    """
    if have_root():
        rc, out, err = _run(argv)
        return Result(rc == 0, out if rc == 0 else err)

    if passwordless_sudo():
        rc, out, err = _run(["sudo", "-n"] + argv)
        return Result(rc == 0, out if rc == 0 else err)

    if allow_pkexec and shutil.which("pkexec"):
        rc, out, err = _run(["pkexec"] + argv)
        if rc == 0:
            return Result(True, out)
        low = (err or "").lower()
        if any(m in low for m in _NO_AGENT_MARKERS):
            pass                                  # fall through and ask
        elif rc == 126 or any(m in low for m in _DISMISSED_MARKERS):
            return Result(False, "Authentication was cancelled.", cancelled=True)
        elif rc != 127:
            return Result(False, err or out)      # the command itself failed

    if ask_password is None:
        return Result(False,
                      "Could not get root access: no authentication agent is "
                      "available. Run this tool from a desktop session, or as "
                      "root.")
    if not shutil.which("sudo"):
        return Result(False, "Could not get root access: sudo is not installed "
                             "and no authentication agent is available.")

    for attempt in range(SUDO_ATTEMPTS):
        password = ask_password("Administrator access is required to change "
                                "display settings.", attempt > 0)
        if password is None:
            return Result(False, "Authentication was cancelled.", cancelled=True)
        rc, out, err = _run(["sudo", "-S", "-p", ""] + argv,
                            stdin_text=password + "\n")
        del password
        if rc == 0:
            return Result(True, out)
        low = (err or "").lower()
        if "incorrect password" in low or "sorry, try again" in low:
            continue                              # ask again
        return Result(False, err or out)

    return Result(False, "Authentication failed.")
