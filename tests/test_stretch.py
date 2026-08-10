"""Tests for the KWin stretched-resolution backend.

The signature scan is checked against the real libkwin on this machine
when present (that is the whole point -- it must survive a KWin update),
and the login-env file management is checked in a temp dir.
"""

import glob
import os
import re
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from linux_cru import stretch


def _libkwins():
    return sorted(glob.glob("/usr/lib/libkwin.so.6*")
                  + glob.glob("/usr/lib*/libkwin.so.6*"))


def test_signature_finds_the_static_in_real_libkwin():
    libs = [p for p in _libkwins() if os.path.isfile(p) and not os.path.islink(p)]
    if not libs:
        print("no libkwin on this machine - skipping")
        return
    for path in libs:
        with open(path, "rb") as f:
            code = f.read()
        res = stretch._find_static(code)
        assert res is not None, f"signature did not match {path}"
        engaged, value = res
        # the invariant the scan relies on
        assert engaged - value == 8, (path, hex(engaged), hex(value))
        # both must land in a plausible data range, not the code
        assert value > 0x900000, (path, hex(value))
        print(f"{os.path.basename(path)}: engaged={hex(engaged)} value={hex(value)}")


def test_invariant_rejects_a_coincidental_match():
    # a cmpb/mov pair whose refs are NOT 8 apart must be ignored
    fake = (b"\x80\x3d\x00\x00\x00\x00\x00"      # cmpb, disp 0
            b"\x4c\x8b\x05\x00\x00\x00\x00")     # mov, disp 0 -> refs equal, gap 0
    assert stretch._find_static(fake) is None


def test_synthetic_signature_is_parsed_correctly():
    # cmpb at 0, disp32 chosen so engaged = 0x1008; mov right after so
    # value = 0x1000 (8 below). Verifies the offset arithmetic.
    # cmpb ends at 7 -> engaged = 7 + disp_e => disp_e = 0x1008-7 = 0x1001
    # mov starts at 7, ends at 14 -> value = 14 + disp_v => disp_v = 0x1000-14 = 0xFF2
    cmpb = b"\x80\x3d" + struct.pack("<i", 0x1001) + b"\x00"
    mov = b"\x4c\x8b\x05" + struct.pack("<i", 0xFF2)
    res = stretch._find_static(cmpb + mov)
    assert res == (0x1008, 0x1000), [hex(x) for x in res] if res else None


def test_signature_tolerates_scheduled_instructions_between():
    # real KWin has "mov r14, rdi; je" between the cmpb and the mov
    cmpb = b"\x80\x3d" + struct.pack("<i", 0) + b"\x00"   # engaged = 7
    gap = b"\x49\x89\xfe\x0f\x84\x00\x00\x00\x00"          # mov r14,rdi; je
    # value must be engaged-8 = -1; mov ends at len(cmpb)+len(gap)+7
    mov_off = len(cmpb) + len(gap)
    disp_v = (7 - 8) - (mov_off + 7)
    mov = b"\x4c\x8b\x05" + struct.pack("<i", disp_v)
    res = stretch._find_static(cmpb + gap + mov)
    assert res == (7 - 1 + 1, 7 - 8) or res == (-1 + 8, -1), res  # engaged-value==8
    assert res[0] - res[1] == 8


def test_persistent_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        stretch.ENVIRONMENT_D_FILE = os.path.join(d, "env", "linux-cru-stretch.conf")
        assert stretch.persistent_value() is None
        stretch.write_persistent("FULL")
        assert os.path.exists(stretch.ENVIRONMENT_D_FILE)
        assert stretch.persistent_value() == "FULL"
        body = open(stretch.ENVIRONMENT_D_FILE).read()
        assert "KWIN_DRM_FORCE_SCALING_MODE=FULL" in body
        assert "Linux CRU" in body
        assert stretch.remove_persistent()
        assert stretch.persistent_value() is None
        assert not stretch.remove_persistent()  # already gone


def test_live_patch_script_is_valid_python_and_carries_the_enum():
    for value, enum in [("full", 1), ("none", 0), ("full_aspect", 3)]:
        s = stretch.build_live_patch_script(value)
        compile(s, "<gen>", "exec")            # must be valid python
        assert f"ENUM = {enum}" in s
        assert "e - v == 8" in s                            # the invariant
        assert "/proc/" in s and "mem" in s                  # scans live memory


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
