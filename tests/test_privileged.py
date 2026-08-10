"""Root-authentication routing tests.

No real privilege is used: the subprocess runner and the environment
probes are replaced, so every branch (already root, passwordless sudo,
pkexec, password prompt, cancellation, wrong password) is exercised
offline.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from linux_cru import privileged


class Fake:
    """Stand-in for the module's process runner and probes."""

    def __init__(self, responses, available=("pkexec", "sudo"), root=False):
        self.responses = responses      # list of (rc, out, err), consumed in order
        self.calls = []
        self.available = available
        self.root = root

    def run(self, argv, stdin_text=None):
        self.calls.append((argv, stdin_text))
        if not self.responses:
            return 0, "", ""
        return self.responses.pop(0)

    def which(self, name):
        return f"/usr/bin/{name}" if name in self.available else None

    def install(self):
        privileged._run = self.run
        privileged.shutil.which = self.which
        privileged.os.geteuid = lambda: 0 if self.root else 1000


def restore():
    import importlib
    importlib.reload(privileged)


def test_already_root_runs_directly():
    f = Fake([(0, "done", "")], root=True)
    f.install()
    try:
        r = privileged.run_as_root(["/bin/bash", "x.sh"])
        assert r.ok and r.message == "done"
        assert f.calls[0][0] == ["/bin/bash", "x.sh"], f.calls
    finally:
        restore()


def test_passwordless_sudo_preferred():
    # first call: `sudo -n true` probe succeeds, then the real command
    f = Fake([(0, "", ""), (0, "ok", "")])
    f.install()
    try:
        r = privileged.run_as_root(["/bin/bash", "x.sh"])
        assert r.ok, r.message
        assert f.calls[0][0] == ["sudo", "-n", "true"]
        assert f.calls[1][0] == ["sudo", "-n", "/bin/bash", "x.sh"]
    finally:
        restore()


def test_pkexec_used_when_sudo_needs_password():
    f = Fake([(1, "", ""),            # sudo -n probe fails
              (0, "applied", "")])    # pkexec succeeds
    f.install()
    try:
        r = privileged.run_as_root(["/bin/bash", "x.sh"])
        assert r.ok and r.message == "applied"
        assert f.calls[1][0][0] == "pkexec"
    finally:
        restore()


def test_pkexec_dismissed_is_cancelled_not_failure():
    f = Fake([(1, "", ""),
              (126, "", "Error executing command as another user: "
                        "Request dismissed")])
    f.install()
    try:
        asked = []
        r = privileged.run_as_root(["/bin/bash", "x.sh"],
                                   ask_password=lambda m, retry: asked.append(1))
        assert not r.ok
        assert r.cancelled, "dismissing the polkit dialog must read as cancelled"
        assert not asked, "must not fall back to a password prompt after a dismissal"
    finally:
        restore()


def test_no_polkit_agent_falls_back_to_password_prompt():
    f = Fake([(1, "", ""),                                   # sudo -n fails
              (127, "", "No authentication agent found."),   # pkexec, no agent
              (0, "applied", "")])                           # sudo -S works
    f.install()
    try:
        prompts = []

        def ask(message, retry):
            prompts.append((message, retry))
            return "hunter2"

        r = privileged.run_as_root(["/bin/bash", "x.sh"], ask_password=ask)
        assert r.ok, r.message
        assert len(prompts) == 1 and prompts[0][1] is False
        argv, stdin = f.calls[-1]
        assert argv[:3] == ["sudo", "-S", "-p"], argv
        assert stdin == "hunter2\n"
    finally:
        restore()


def test_pkexec_missing_falls_back_to_password_prompt():
    f = Fake([(1, "", ""), (0, "applied", "")], available=("sudo",))
    f.install()
    try:
        r = privileged.run_as_root(["/bin/bash", "x.sh"],
                                   ask_password=lambda m, retry: "pw")
        assert r.ok, r.message
        assert not any(c[0][0] == "pkexec" for c in f.calls)
    finally:
        restore()


def test_wrong_password_retries_then_gives_up():
    f = Fake([(1, "", "")] + [(1, "", "Sorry, try again.")] * 3,
             available=("sudo",))
    f.install()
    try:
        prompts = []

        def ask(message, retry):
            prompts.append(retry)
            return "wrong"

        r = privileged.run_as_root(["/bin/bash", "x.sh"], ask_password=ask)
        assert not r.ok and not r.cancelled
        assert len(prompts) == privileged.SUDO_ATTEMPTS, prompts
        assert prompts[0] is False and prompts[1] is True, prompts
    finally:
        restore()


def test_cancelling_password_prompt():
    f = Fake([(1, "", "")], available=("sudo",))
    f.install()
    try:
        r = privileged.run_as_root(["/bin/bash", "x.sh"],
                                   ask_password=lambda m, retry: None)
        assert not r.ok and r.cancelled
        assert "cancelled" in r.message.lower()
    finally:
        restore()


def test_no_agent_and_no_prompt_explains_itself():
    f = Fake([(1, "", ""), (127, "", "No authentication agent found.")])
    f.install()
    try:
        r = privileged.run_as_root(["/bin/bash", "x.sh"])
        assert not r.ok and not r.cancelled
        assert "root" in r.message.lower()
    finally:
        restore()


def test_command_failure_is_not_an_auth_problem():
    f = Fake([(1, "", ""), (3, "", "mkinitcpio: no such hook")])
    f.install()
    try:
        asked = []
        r = privileged.run_as_root(["/bin/bash", "x.sh"],
                                   ask_password=lambda m, retry: asked.append(1))
        assert not r.ok and not r.cancelled
        assert "mkinitcpio" in r.message
        assert not asked, "a failing command must not trigger a password prompt"
    finally:
        restore()


def test_result_unpacks_like_a_tuple():
    r = privileged.Result(True, "hi")
    ok, message = r
    assert ok and message == "hi" and bool(r)


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
