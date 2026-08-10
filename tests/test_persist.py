"""Persistence tests.

The generated root scripts are exercised against a fake filesystem
tree (module path constants are redirected into a temp directory), so
install/uninstall can be tested end to end without touching the real
system or needing root.
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from linux_cru import persist


def _sandbox(root, bootloader="limine"):
    """Point the module's paths into `root` and build the fake tree."""
    persist.FIRMWARE_DIR = f"{root}/usr/lib/firmware/edid"
    persist.MKINITCPIO_DROPIN = f"{root}/etc/mkinitcpio.conf.d/99-linux-cru.conf"
    persist.DRACUT_DROPIN = f"{root}/etc/dracut.conf.d/99-linux-cru.conf"
    persist.INITRAMFS_TOOLS_HOOK = f"{root}/etc/initramfs-tools/hooks/linux-cru"
    persist.GRUB_DEFAULT = f"{root}/etc/default/grub"
    persist.LIMINE_DEFAULT = f"{root}/etc/default/limine"
    persist.KERNEL_CMDLINE = f"{root}/etc/kernel/cmdline"

    os.makedirs(f"{root}/etc/default", exist_ok=True)
    os.makedirs(f"{root}/etc/mkinitcpio.conf.d", exist_ok=True)
    if bootloader == "limine":
        with open(persist.LIMINE_DEFAULT, "w") as f:
            f.write('ESP_PATH="/boot"\n'
                    'KERNEL_CMDLINE[default]+="quiet splash root=UUID=abc"\n'
                    'BOOT_ORDER="*, *lts"\n')
    elif bootloader == "grub":
        with open(persist.GRUB_DEFAULT, "w") as f:
            f.write('GRUB_TIMEOUT=5\n'
                    'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"\n')


def _run(script, root):
    """Run a generated script with mkinitcpio/limine-update stubbed out."""
    bindir = f"{root}/stubbin"
    os.makedirs(bindir, exist_ok=True)
    for tool in ("mkinitcpio", "limine-update", "grub-mkconfig", "dracut",
                 "update-initramfs"):
        p = f"{bindir}/{tool}"
        with open(p, "w") as f:
            f.write(f'#!/bin/sh\necho "STUB {tool} $*" >> "{root}/tools.log"\n')
        os.chmod(p, 0o755)

    path = f"{root}/script.sh"
    # /etc/mkinitcpio.conf presence drives the initramfs branch; fake it
    with open(path, "w") as f:
        f.write(script.replace("/etc/mkinitcpio.conf ",
                               f"{root}/etc/mkinitcpio.conf "))
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
    return subprocess.run(["bash", path], capture_output=True,
                          universal_newlines=True, env=env)


def test_install_then_uninstall_limine():
    root = tempfile.mkdtemp(prefix="cru-persist-")
    try:
        _sandbox(root, "limine")
        open(f"{root}/etc/mkinitcpio.conf", "w").write("FILES=()\n")
        edid = f"{root}/patched.bin"
        with open(edid, "wb") as f:
            f.write(b"\x00" * 256)

        res = _run(persist.build_install_script("DP-1", edid), root)
        assert res.returncode == 0, res.stderr

        installed = persist.firmware_path("DP-1")
        assert os.path.exists(installed), "EDID not installed"

        limine = open(persist.LIMINE_DEFAULT).read()
        assert "drm.edid_firmware=DP-1:edid/linux-cru-DP-1.bin" in limine, limine
        assert 'KERNEL_CMDLINE[default]+="quiet splash root=UUID=abc"' in limine, \
            "original cmdline line was damaged"
        assert os.path.exists(persist.LIMINE_DEFAULT + ".linux-cru-backup")

        dropin = open(persist.MKINITCPIO_DROPIN).read()
        assert "linux-cru-DP-1.bin" in dropin and "FILES+=(" in dropin, dropin
        tools = open(f"{root}/tools.log").read()
        assert "STUB mkinitcpio -P" in tools and "STUB limine-update" in tools, tools

        # second display: entries must merge into one parameter
        res = _run(persist.build_install_script("HDMI-A-1", edid), root)
        assert res.returncode == 0, res.stderr
        limine = open(persist.LIMINE_DEFAULT).read()
        params = [l for l in limine.splitlines() if "drm.edid_firmware" in l]
        assert len(params) == 1, f"expected one managed line, got: {params}"
        assert "DP-1:edid/linux-cru-DP-1.bin" in params[0]
        assert "HDMI-A-1:edid/linux-cru-HDMI-A-1.bin" in params[0]
        assert params[0].count("drm.edid_firmware=") == 1

        # uninstall one: the other survives
        res = _run(persist.build_uninstall_script("DP-1"), root)
        assert res.returncode == 0, res.stderr
        assert not os.path.exists(persist.firmware_path("DP-1"))
        assert os.path.exists(persist.firmware_path("HDMI-A-1"))
        limine = open(persist.LIMINE_DEFAULT).read()
        assert "DP-1:edid" not in limine and "HDMI-A-1:edid" in limine, limine

        # uninstall the rest: config returns to its original shape
        res = _run(persist.build_uninstall_script("HDMI-A-1"), root)
        assert res.returncode == 0, res.stderr
        limine = open(persist.LIMINE_DEFAULT).read()
        assert "drm.edid_firmware" not in limine, limine
        assert "linux-cru" not in limine, "marker comment left behind"
        original = open(persist.LIMINE_DEFAULT + ".linux-cru-backup").read()
        assert limine.strip() == original.strip(), \
            f"config not restored:\n{limine!r}\nvs\n{original!r}"
        assert not os.path.exists(persist.MKINITCPIO_DROPIN), "drop-in left behind"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_install_then_uninstall_grub():
    root = tempfile.mkdtemp(prefix="cru-persist-")
    try:
        _sandbox(root, "grub")
        open(f"{root}/etc/mkinitcpio.conf", "w").write("FILES=()\n")
        edid = f"{root}/patched.bin"
        with open(edid, "wb") as f:
            f.write(b"\x00" * 256)

        res = _run(persist.build_install_script("DP-1", edid), root)
        assert res.returncode == 0, res.stderr
        grub = open(persist.GRUB_DEFAULT).read()
        assert "drm.edid_firmware=DP-1:edid/linux-cru-DP-1.bin" in grub, grub
        assert 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"' in grub, \
            "original GRUB line was damaged"
        # the managed line must reference the original variable, not replace it
        managed = [l for l in grub.splitlines() if "drm.edid_firmware" in l][0]
        assert "$GRUB_CMDLINE_LINUX_DEFAULT" in managed, managed
        assert "STUB grub-mkconfig" in open(f"{root}/tools.log").read()

        res = _run(persist.build_uninstall_script("DP-1"), root)
        assert res.returncode == 0, res.stderr
        grub = open(persist.GRUB_DEFAULT).read()
        assert "linux-cru" not in grub and "drm.edid_firmware" not in grub, grub
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_missing_edid_file_fails_cleanly():
    root = tempfile.mkdtemp(prefix="cru-persist-")
    try:
        _sandbox(root, "limine")
        res = _run(persist.build_install_script("DP-1", f"{root}/nope.bin"), root)
        assert res.returncode != 0
        assert "missing or empty" in res.stderr
        limine = open(persist.LIMINE_DEFAULT).read()
        assert "drm.edid_firmware" not in limine, "config touched despite failure"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_detect_and_describe_on_this_machine():
    # These read the real system read-only.
    import importlib
    importlib.reload(persist)
    boot, detail = persist.detect_bootloader()
    initrd, idetail = persist.detect_initramfs()
    print(f"this machine: bootloader={boot} ({detail}), initramfs={initrd}")
    assert boot in ("grub", "limine", "systemd-boot-uki", "systemd-boot", "unknown")
    steps = persist.describe_plan("DP-1")
    assert len(steps) == 3
    for s in steps:
        print("  -", s)


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
