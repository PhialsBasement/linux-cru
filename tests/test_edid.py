"""EDID engine tests.

Pure tests always run. Tests marked "real" use the EDID of a connected
display on this machine (skipped when unavailable), and tests using
edid-decode are skipped when the binary is missing.
"""

import glob
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from linux_cru import timings
from linux_cru.edid import Edid, EdidError, BLOCK_SIZE, HEADER


def make_base_edid():
    """Minimal valid base block: header, version 1.4, four dummy slots."""
    b = bytearray(BLOCK_SIZE)
    b[0:8] = HEADER
    b[8:10] = (0x04, 0x69)      # manufacturer id
    b[18], b[19] = 1, 4          # EDID 1.4
    b[20] = 0xA5                 # digital input
    b[35:37] = (0x01, 0x01)
    for off in (54, 72, 90, 108):
        b[off + 3] = 0x10        # dummy descriptor
    e = Edid(bytes(b))
    e.fix_checksums()
    return e


def _real_edid_path():
    for p in sorted(glob.glob("/sys/class/drm/card*-*/edid")):
        try:
            with open(p, "rb") as f:
                if len(f.read()) >= 128:
                    return p
        except OSError:
            pass
    return None


def _edid_decode(data):
    return subprocess.run(["edid-decode", "--check"], input=data,
                          capture_output=True).stdout.decode(errors="replace")


# -- pure tests ---------------------------------------------------------------

def test_reject_garbage():
    for bad in (b"", b"\x00" * 100, b"\x01" * 128):
        try:
            Edid(bad)
        except EdidError:
            continue
        raise AssertionError(f"accepted invalid data of length {len(bad)}")


def test_dtd_roundtrip():
    ml = timings.calc(1920, 1080, 75, "cvt-rb2")
    dtd = Edid.encode_dtd(ml)
    assert len(dtd) == 18
    info = Edid.decode_dtd(dtd)
    assert info["width"] == 1920 and info["height"] == 1080
    assert abs(info["refresh"] - ml.actual_refresh) < 0.05
    assert info["pixel_clock_khz"] == round(ml.clock_khz, -1)


def test_type1_roundtrip():
    ml = timings.calc(2560, 1440, 240, "cvt-rb2")
    rec = Edid.encode_type1(ml)
    assert len(rec) == 20
    info = Edid.decode_type1(rec)
    assert info["width"] == 2560 and info["height"] == 1440
    assert abs(info["refresh"] - ml.actual_refresh) < 0.05


def test_dtd_limits():
    big = timings.calc(2560, 1440, 240, "cvt-rb2")  # > 655.35 MHz
    try:
        Edid.encode_dtd(big)
    except EdidError:
        pass
    else:
        raise AssertionError("expected EdidError for pclk over DTD limit")


def test_add_small_mode_uses_base_slot():
    e = make_base_edid()
    ml = timings.calc(1920, 1080, 75, "cvt-rb2")
    assert e.add_mode(ml) == "base-dtd"
    modes = e.list_modes()
    assert any(m.width == 1920 and m.height == 1080 and m.location == "base-dtd"
               for m in modes), modes
    assert e.checksum_errors() == [] or e.to_bytes()  # fix on save
    assert Edid(e.to_bytes()).checksum_errors() == []


def test_add_big_mode_creates_displayid_block():
    e = make_base_edid()
    ml = timings.calc(2560, 1440, 240, "cvt-rb2")
    assert e.add_mode(ml) == "displayid"
    data = e.to_bytes()
    assert len(data) == 2 * BLOCK_SIZE
    assert data[126] == 1                      # extension count updated
    assert data[BLOCK_SIZE] == 0x70            # DisplayID tag
    assert data[BLOCK_SIZE + 1] == 0x12        # structure version 1.2
    payload_size = data[BLOCK_SIZE + 2]
    assert payload_size == 23                  # 3-byte header + one record
    # section checksum: bytes 1..(5+size) sum to 0 mod 256
    section = data[BLOCK_SIZE + 1:BLOCK_SIZE + 5 + payload_size + 1]
    assert sum(section) % 256 == 0
    # whole block checksum
    assert sum(data[BLOCK_SIZE:2 * BLOCK_SIZE]) % 256 == 0
    modes = Edid(data).list_modes()
    assert any(m.width == 2560 and abs(m.refresh - ml.actual_refresh) < 0.05
               and m.location == "displayid" for m in modes), modes


def test_fill_base_slots_then_spill():
    e = make_base_edid()
    for i, r in enumerate((60, 75, 100, 120)):
        assert e.add_mode(timings.calc(1920, 1080, r, "cvt-rb2")) == "base-dtd"
    # all four slots used, no CTA block -> next goes to DisplayID
    assert e.add_mode(timings.calc(1920, 1080, 144, "cvt-rb2")) == "displayid"
    assert len(e.list_modes()) == 5


# -- real-hardware tests ---------------------------------------------------------

def test_identity_and_limits_from_real_edid():
    path = _real_edid_path()
    if not path:
        print("no connected display with EDID - skipping")
        return
    with open(path, "rb") as f:
        info = Edid(f.read()).info()
    assert info.manufacturer and info.manufacturer.isalpha(), info
    assert len(info.manufacturer) == 3, info
    assert info.version.startswith("1."), info
    assert info.summary(), "the interface needs something to show"
    print(f"identity: {info.summary()}")
    if info.max_pixel_clock_mhz:
        assert 25 <= info.max_pixel_clock_mhz <= 2000, info


def test_identity_of_a_synthetic_edid():
    e = make_base_edid()
    # "ABC", product 0x1234, made week 10 of 2020, 60x34 cm
    e.data[8], e.data[9] = 0x04, 0x43
    e.data[10], e.data[11] = 0x34, 0x12
    e.data[16], e.data[17] = 10, 30
    e.data[21], e.data[22] = 60, 34
    name = b"Test Monitor\n"
    e.data[54:72] = bytes([0, 0, 0, 0xFC, 0]) + name.ljust(13, b" ")
    e.data[72:90] = bytes([0, 0, 0, 0xFD, 0, 50, 75, 30, 83, 17]) + b"\n" + b" " * 7
    info = e.info()
    assert info.manufacturer == "ABC", info.manufacturer
    assert info.product_code == 0x1234
    assert info.year == 2020 and info.week == 10
    assert info.name == "Test Monitor", repr(info.name)
    assert (info.min_vrefresh, info.max_vrefresh) == (50, 75), info
    assert (info.min_hsync_khz, info.max_hsync_khz) == (30, 83), info
    assert info.max_pixel_clock_mhz == 170, info
    assert "Test Monitor" in info.summary()


def test_real_edid_roundtrip_and_parse():
    path = _real_edid_path()
    if not path:
        print("no connected display with EDID - skipping real tests")
        return
    with open(path, "rb") as f:
        raw = f.read()
    e = Edid(raw)
    assert e.checksum_errors() == [], "real EDID has bad checksums?"
    assert e.to_bytes() == raw, "round-trip changed an untouched EDID"
    modes = e.list_modes()
    assert modes, "no modes parsed from real EDID"
    print(f"real EDID: {len(raw)//128} blocks, modes parsed:")
    for m in modes:
        print(f"  {m.width}x{m.height} @ {m.refresh:.3f} Hz "
              f"({m.pixel_clock_khz/1000:.2f} MHz, {m.location})")


def test_real_edid_add_modes_and_validate():
    path = _real_edid_path()
    if not path or not shutil.which("edid-decode"):
        print("skipping edid-decode validation test")
        return
    with open(path, "rb") as f:
        raw = f.read()

    before = _edid_decode(raw)
    fails_before = before.count("FAIL")

    e = Edid(raw)
    small = timings.calc(1920, 1080, 75, "cvt-rb2")
    big = timings.calc(2560, 1440, 240, "cvt-rb2")
    loc_small = e.add_mode(small)
    loc_big = e.add_mode(big)
    print(f"placed 1080p75 in {loc_small}, 1440p240 in {loc_big}")
    assert loc_big == "displayid"

    data = e.to_bytes()
    after = _edid_decode(data)
    assert "1920x1080" in after and "75.0" in after, "small mode not visible"
    assert "2560x1440" in after, "big mode not visible"
    fails_after = after.count("FAIL")
    assert fails_after <= fails_before, (
        f"patched EDID has NEW conformance failures ({fails_before} -> {fails_after}):\n"
        + after[-2000:])
    print(f"edid-decode conformance: {fails_before} fails before, {fails_after} after")


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as ex:
                failures += 1
                print(f"FAIL {name}: {ex}")
    sys.exit(1 if failures else 0)
