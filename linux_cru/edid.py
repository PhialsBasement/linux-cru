"""EDID parsing and patching.

Reads an EDID (from a DRM connector or a file), decodes the timings it
carries, and adds new modes the way Windows CRU does (see
docs/research/cru-source-notes.md for the byte-level reference):

- as an 18-byte detailed timing descriptor (DTD) in a free base-block
  descriptor slot or in the CTA-861 extension's DTD area, when the
  timing fits the DTD format (pixel clock at most 655.35 MHz,
  dimensions at most 4095); or
- as a 20-byte DisplayID Type I record in a DisplayID extension
  (created if missing), for anything larger.

Everything already in the EDID is preserved byte-for-byte except the
checksums, which are recomputed on save.
"""

from dataclasses import dataclass

from .timings import Modeline

BLOCK_SIZE = 128
HEADER = bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])

DTD_MAX_PCLK_10KHZ = 65535       # 16-bit field: 655.35 MHz
DTD_MAX_ACTIVE = 4095            # 12-bit fields
DTD_MAX_BLANK = 4095
DTD_MAX_HFRONT = 1023
DTD_MAX_HSYNC = 1023
DTD_MAX_VFRONT = 63
DTD_MAX_VSYNC = 63

DISPLAYID_TAG = 0x70
CTA_TAG = 0x02
DISPLAYID_MAX_PAYLOAD = 121      # 128 - 5 header - 2 checksums
TYPE1_TAG = 0x03

BASE_DESCRIPTOR_OFFSETS = (54, 72, 90, 108)


class EdidError(ValueError):
    pass


def _type1_aspect(width, height):
    """DisplayID Type I aspect-ratio code (flags bits 0-3)."""
    ratios = {(1, 1): 0, (5, 4): 1, (4, 3): 2, (15, 9): 3, (16, 9): 4,
              (16, 10): 5, (64, 27): 6, (256, 135): 7}
    for (a, b), code in ratios.items():
        if width * b == height * a:
            return code
    return 8  # undefined / other


@dataclass(frozen=True)
class EdidMode:
    width: int
    height: int
    refresh: float
    pixel_clock_khz: int
    location: str        # "base-dtd" / "cta-dtd" / "displayid"


@dataclass(frozen=True)
class EdidInfo:
    """Who the display says it is, and what it says it can do."""
    manufacturer: str = ""
    product_code: int = 0
    serial_number: int = 0
    week: int = 0
    year: int = 0
    version: str = ""
    name: str = ""
    serial_text: str = ""
    width_cm: int = 0
    height_cm: int = 0
    # Declared limits, from the range descriptor. These are what a driver
    # checks a custom mode against, so they are the numbers that decide
    # whether an overclock is accepted.
    min_vrefresh: int = 0
    max_vrefresh: int = 0
    min_hsync_khz: int = 0
    max_hsync_khz: int = 0
    max_pixel_clock_mhz: int = 0

    @property
    def display_name(self):
        if self.name:
            return self.name
        if self.manufacturer:
            return f"{self.manufacturer} {self.product_code:04X}"
        return "unknown display"

    def summary(self):
        parts = [self.display_name]
        if self.width_cm and self.height_cm:
            inches = ((self.width_cm ** 2 + self.height_cm ** 2) ** 0.5) / 2.54
            parts.append(f"{self.width_cm}x{self.height_cm} cm ({inches:.0f} in)")
        if self.year:
            parts.append(f"made {self.year}")
        if self.max_vrefresh:
            limits = f"{self.min_vrefresh}-{self.max_vrefresh} Hz"
            if self.max_hsync_khz:
                limits += f", {self.min_hsync_khz}-{self.max_hsync_khz} kHz"
            if self.max_pixel_clock_mhz:
                limits += f", up to {self.max_pixel_clock_mhz} MHz"
            parts.append("declared limits: " + limits)
        return "   ".join(parts)


class Edid:
    def __init__(self, data: bytes):
        if len(data) < BLOCK_SIZE or len(data) % BLOCK_SIZE != 0:
            raise EdidError(f"EDID length {len(data)} is not a multiple of 128")
        if bytes(data[:8]) != HEADER:
            raise EdidError("not an EDID: bad header")
        self.data = bytearray(data)

    @classmethod
    def from_connector(cls, connector):
        """Load from a DRM connector name like 'card1-DP-1'."""
        path = f"/sys/class/drm/{connector}/edid"
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            raise EdidError(f"cannot read the EDID of {connector}: {e}") from e
        if not data:
            raise EdidError(f"{connector} has no EDID (display disconnected?)")
        return cls(data)

    # -- blocks ---------------------------------------------------------------

    @property
    def block_count(self):
        return len(self.data) // BLOCK_SIZE

    def block(self, index):
        return memoryview(self.data)[index * BLOCK_SIZE:(index + 1) * BLOCK_SIZE]

    def extension_tag(self, index):
        """Tag byte of extension block `index` (1-based block number)."""
        return self.data[index * BLOCK_SIZE]

    def _find_extension(self, tag):
        for i in range(1, self.block_count):
            if self.extension_tag(i) == tag:
                return i
        return None

    # -- checksums -------------------------------------------------------------

    def fix_checksums(self):
        for i in range(self.block_count):
            start = i * BLOCK_SIZE
            self.data[start + 127] = (256 - sum(self.data[start:start + 127])) % 256
            if i > 0 and self.data[start] == DISPLAYID_TAG:
                self._fix_displayid_section_checksum(i)

    def _fix_displayid_section_checksum(self, index):
        start = index * BLOCK_SIZE
        size = self.data[start + 2]
        pos = start + 5 + size
        if pos >= start + 127:
            return
        self.data[pos] = 0
        self.data[pos] = (256 - sum(self.data[start + 1:pos + 1])) % 256

    def checksum_errors(self):
        """List of block indexes whose checksum is wrong."""
        bad = []
        for i in range(self.block_count):
            start = i * BLOCK_SIZE
            if sum(self.data[start:start + BLOCK_SIZE]) % 256 != 0:
                bad.append(i)
        return bad

    def to_bytes(self):
        self.fix_checksums()
        return bytes(self.data)

    # -- DTD encode/decode -----------------------------------------------------

    @staticmethod
    def encode_dtd(ml: Modeline, width_mm: int = 0, height_mm: int = 0) -> bytes:
        pclk = int(round(ml.clock_khz / 10.0))
        h_front = ml.hsync_start - ml.hdisplay
        h_sync = ml.hsync_end - ml.hsync_start
        h_blank = ml.htotal - ml.hdisplay
        v_front = ml.vsync_start - ml.vdisplay
        v_sync = ml.vsync_end - ml.vsync_start
        v_blank = ml.vtotal - ml.vdisplay

        if pclk > DTD_MAX_PCLK_10KHZ:
            raise EdidError("pixel clock over 655.35 MHz does not fit a DTD")
        if ml.hdisplay > DTD_MAX_ACTIVE or ml.vdisplay > DTD_MAX_ACTIVE \
                or h_blank > DTD_MAX_BLANK or v_blank > DTD_MAX_BLANK \
                or h_front > DTD_MAX_HFRONT or h_sync > DTD_MAX_HSYNC \
                or v_front > DTD_MAX_VFRONT or v_sync > DTD_MAX_VSYNC:
            raise EdidError("timing values do not fit the DTD field limits")

        # Bytes 12-14 are the physical size of the picture in millimetres,
        # which is what a display uses to work out DPI and how to scale.
        # CRU writes active/4 here instead, which claims a 1920x1080 mode
        # is 480x270 mm whatever panel it is on. Use the real size from
        # the base block, and only fall back to the pixel-derived number
        # when the display does not state one.
        img_h = width_mm or ml.hdisplay // 4
        img_v = height_mm or ml.vdisplay // 4

        d = bytearray(18)
        d[0] = pclk & 0xFF
        d[1] = pclk >> 8
        d[2] = ml.hdisplay & 0xFF
        d[3] = h_blank & 0xFF
        d[4] = ((ml.hdisplay & 0xF00) >> 4) | ((h_blank & 0xF00) >> 8)
        d[5] = ml.vdisplay & 0xFF
        d[6] = v_blank & 0xFF
        d[7] = ((ml.vdisplay & 0xF00) >> 4) | ((v_blank & 0xF00) >> 8)
        d[8] = h_front & 0xFF
        d[9] = h_sync & 0xFF
        d[10] = ((v_front & 0x0F) << 4) | (v_sync & 0x0F)
        d[11] = (((h_front & 0x300) >> 2) | ((h_sync & 0x300) >> 4)
                 | ((v_front & 0x30) >> 2) | ((v_sync & 0x30) >> 4))
        d[12] = img_h & 0xFF
        d[13] = img_v & 0xFF
        d[14] = ((img_h & 0xF00) >> 4) | ((img_v & 0xF00) >> 8)
        # bytes 15-16: borders, zero
        d[17] = 0x18 | (0x02 if ml.hsync_positive else 0) \
                     | (0x04 if ml.vsync_positive else 0)
        return bytes(d)

    @staticmethod
    def decode_dtd(d):
        """Decode an 18-byte DTD; None if it is not a timing descriptor."""
        if d[0] == 0 and d[1] == 0:
            return None
        pclk_khz = (d[0] | (d[1] << 8)) * 10
        hactive = d[2] | ((d[4] & 0xF0) << 4)
        hblank = d[3] | ((d[4] & 0x0F) << 8)
        vactive = d[5] | ((d[7] & 0xF0) << 4)
        vblank = d[6] | ((d[7] & 0x0F) << 8)
        interlaced = bool(d[17] & 0x80)
        htotal = hactive + hblank
        vtotal = vactive + vblank
        if htotal == 0 or vtotal == 0:
            return None
        refresh = pclk_khz * 1000.0 / (htotal * vtotal)
        if interlaced:
            vactive *= 2
            refresh *= 2
        return {"width": hactive, "height": vactive, "refresh": refresh,
                "pixel_clock_khz": pclk_khz}

    # -- DisplayID Type I encode/decode -------------------------------------------

    @staticmethod
    def encode_type1(ml: Modeline) -> bytes:
        pclk = int(round(ml.clock_khz / 10.0))
        h_front = ml.hsync_start - ml.hdisplay
        h_sync = ml.hsync_end - ml.hsync_start
        h_blank = ml.htotal - ml.hdisplay
        v_front = ml.vsync_start - ml.vdisplay
        v_sync = ml.vsync_end - ml.vsync_start
        v_blank = ml.vtotal - ml.vdisplay

        r = bytearray(20)
        clk = pclk - 1
        r[0] = clk & 0xFF
        r[1] = (clk >> 8) & 0xFF
        r[2] = (clk >> 16) & 0xFF
        # flags bits 0-3: aspect ratio; rest: not interlaced/stereo/preferred
        r[3] = _type1_aspect(ml.hdisplay, ml.vdisplay)
        for offset, value in ((4, ml.hdisplay), (6, h_blank),
                              (10, h_sync), (12, ml.vdisplay),
                              (14, v_blank), (18, v_sync)):
            v = value - 1
            r[offset] = v & 0xFF
            r[offset + 1] = (v >> 8) & 0xFF
        hf = (h_front - 1) | (0x8000 if ml.hsync_positive else 0)
        r[8] = hf & 0xFF
        r[9] = (hf >> 8) & 0xFF
        vf = (v_front - 1) | (0x8000 if ml.vsync_positive else 0)
        r[16] = vf & 0xFF
        r[17] = (vf >> 8) & 0xFF
        return bytes(r)

    @staticmethod
    def decode_type1(r):
        pclk_khz = ((r[0] | (r[1] << 8) | (r[2] << 16)) + 1) * 10
        hactive = (r[4] | (r[5] << 8)) + 1
        hblank = (r[6] | (r[7] << 8)) + 1
        vactive = (r[12] | (r[13] << 8)) + 1
        vblank = (r[14] | (r[15] << 8)) + 1
        htotal = hactive + hblank
        vtotal = vactive + vblank
        if htotal == 0 or vtotal == 0:
            return None
        refresh = pclk_khz * 1000.0 / (htotal * vtotal)
        return {"width": hactive, "height": vactive, "refresh": refresh,
                "pixel_clock_khz": pclk_khz}

    # -- base block descriptor slots -------------------------------------------

    def base_descriptor_kind(self, offset):
        d = self.data[offset:offset + 18]
        if d[0] or d[1]:
            return "dtd"
        tag = d[3]
        return {0x10: "dummy", 0xFD: "range", 0xFC: "name",
                0xFF: "serial"}.get(tag, "other")

    def free_base_slot(self):
        for offset in BASE_DESCRIPTOR_OFFSETS:
            if self.base_descriptor_kind(offset) == "dummy":
                return offset
        return None

    # -- CTA-861 extension -------------------------------------------------------

    def _cta_free_dtd_offset(self):
        """Offset (within self.data) for a new DTD in the CTA block, or None."""
        bi = self._find_extension(CTA_TAG)
        if bi is None:
            return None
        start = bi * BLOCK_SIZE
        d = self.data[start + 2]  # DTD area start; 0 = no DTDs and no room info
        if d == 0:
            return None
        pos = start + d
        while pos + 18 <= start + 127:
            if self.data[pos] == 0 and self.data[pos + 1] == 0:
                return pos
            pos += 18
        return None

    # -- DisplayID extension --------------------------------------------------------

    def _parse_displayid(self, bi):
        """(structure_version, [(tag, revision, payload_bytes), ...])"""
        start = bi * BLOCK_SIZE
        version = self.data[start + 1]
        size = min(self.data[start + 2], DISPLAYID_MAX_PAYLOAD)
        blobs = []
        pos = start + 5
        end = start + 5 + size
        while pos + 3 <= end:
            tag, rev, length = self.data[pos], self.data[pos + 1], self.data[pos + 2]
            if tag == 0 and length == 0:
                break
            length = min(length, end - pos - 3)
            blobs.append((tag, rev, bytes(self.data[pos + 3:pos + 3 + length])))
            pos += 3 + length
        return version, blobs

    def _write_displayid(self, bi, version, blobs):
        payload = b"".join(bytes([t, r, len(p)]) + p for t, r, p in blobs)
        if len(payload) > DISPLAYID_MAX_PAYLOAD:
            raise EdidError("the DisplayID extension is full")
        start = bi * BLOCK_SIZE
        block = bytearray(BLOCK_SIZE)
        block[0] = DISPLAYID_TAG
        block[1] = version
        block[2] = len(payload)
        block[5:5 + len(payload)] = payload
        self.data[start:start + BLOCK_SIZE] = block
        self._fix_displayid_section_checksum(bi)

    def _add_displayid_block(self):
        if self.block_count >= 4:
            raise EdidError("the display's EDID is full (4 blocks, the maximum) "
                            "and has no room for another mode; remove an "
                            "existing custom mode or free a base timing slot")
        self.data.extend(bytes(BLOCK_SIZE))
        bi = self.block_count - 1
        self._write_displayid(bi, 0x12, [])
        self.data[126] = self.block_count - 1
        return bi

    @staticmethod
    def _blobs_with_record(blobs, record):
        """Append a Type I record to an existing Type I block, or start one."""
        blobs = list(blobs)
        for i, (tag, rev, payload) in enumerate(blobs):
            # keep the data-block payload length in its one byte (<=255)
            if tag == TYPE1_TAG and len(payload) + 20 <= 255:
                blobs[i] = (tag, rev, payload + record)
                return blobs
        blobs.append((TYPE1_TAG, 0x00, record))
        return blobs

    @staticmethod
    def _displayid_payload_size(blobs):
        return sum(3 + len(p) for _, _, p in blobs)

    def _add_type1_record(self, record):
        # Put the record in an existing DisplayID extension that still has
        # room; a display that already ships a packed DisplayID block (many
        # high-refresh monitors do) gets a fresh extension block instead of
        # an "extension is full" error.
        for bi in range(1, self.block_count):
            if self.extension_tag(bi) != DISPLAYID_TAG:
                continue
            version, blobs = self._parse_displayid(bi)
            candidate = self._blobs_with_record(blobs, record)
            if self._displayid_payload_size(candidate) <= DISPLAYID_MAX_PAYLOAD:
                self._write_displayid(bi, version, candidate)
                return
        # no existing DisplayID extension had room -> add another block
        bi = self._add_displayid_block()
        version, blobs = self._parse_displayid(bi)
        self._write_displayid(bi, version,
                              self._blobs_with_record(blobs, record))

    # -- main entry points ---------------------------------------------------------

    def info(self) -> EdidInfo:
        """Identity and declared limits from the base block."""
        d = self.data
        packed = (d[8] << 8) | d[9]
        manufacturer = "".join(
            chr(((packed >> shift) & 0x1F) + ord("A") - 1)
            for shift in (10, 5, 0)) if packed else ""
        if not all("A" <= c <= "Z" for c in manufacturer):
            manufacturer = ""

        fields = {
            "manufacturer": manufacturer,
            "product_code": d[10] | (d[11] << 8),
            "serial_number": d[12] | (d[13] << 8) | (d[14] << 16) | (d[15] << 24),
            "week": d[16] if d[16] != 0xFF else 0,
            "year": d[17] + 1990 if d[17] else 0,
            "version": f"{d[18]}.{d[19]}",
            "width_cm": d[21],
            "height_cm": d[22],
        }

        for offset in BASE_DESCRIPTOR_OFFSETS:
            desc = d[offset:offset + 18]
            if desc[0] or desc[1]:
                continue                       # a timing, not a description
            tag = desc[3]
            if tag in (0xFC, 0xFF):
                text = bytes(desc[5:18]).split(b"\n")[0].decode(
                    "ascii", "replace").strip()
                fields["name" if tag == 0xFC else "serial_text"] = text
            elif tag == 0xFD:
                # EDID 1.4 can push the ranges past 255 with offset flags.
                flags = desc[4]
                v_off = 255 if flags & 0x02 else 0
                v_min_off = 255 if flags & 0x01 else 0
                h_off = 255 if flags & 0x08 else 0
                h_min_off = 255 if flags & 0x04 else 0
                fields.update(
                    min_vrefresh=desc[5] + v_min_off,
                    max_vrefresh=desc[6] + v_off,
                    min_hsync_khz=desc[7] + h_min_off,
                    max_hsync_khz=desc[8] + h_off,
                    max_pixel_clock_mhz=desc[9] * 10 if desc[9] != 0xFF else 0,
                )
        return EdidInfo(**fields)

    def list_modes(self):
        modes = []
        for offset in BASE_DESCRIPTOR_OFFSETS:
            info = self.decode_dtd(self.data[offset:offset + 18])
            if info:
                modes.append(EdidMode(location="base-dtd", **info))
        bi = self._find_extension(CTA_TAG)
        if bi is not None:
            start = bi * BLOCK_SIZE
            d = self.data[start + 2]
            if d:
                pos = start + d
                while pos + 18 <= start + 127:
                    info = self.decode_dtd(self.data[pos:pos + 18])
                    if not info:
                        break
                    modes.append(EdidMode(location="cta-dtd", **info))
                    pos += 18
        for bi in range(1, self.block_count):
            if self.extension_tag(bi) != DISPLAYID_TAG:
                continue
            _, blobs = self._parse_displayid(bi)
            for tag, _, payload in blobs:
                if tag != TYPE1_TAG:
                    continue
                for i in range(0, len(payload) - 19, 20):
                    info = self.decode_type1(payload[i:i + 20])
                    if info:
                        modes.append(EdidMode(location="displayid", **info))
        return modes

    def fits_dtd(self, ml: Modeline):
        try:
            self.encode_dtd(ml)
            return True
        except EdidError:
            return False

    def physical_size_mm(self):
        """(width, height) of the panel in mm, or (0, 0) if not stated."""
        return self.data[21] * 10, self.data[22] * 10

    def add_mode(self, ml: Modeline):
        """Add a timing; returns where it was placed
        ("base-dtd" / "cta-dtd" / "displayid")."""
        if self.fits_dtd(ml):
            dtd = self.encode_dtd(ml, *self.physical_size_mm())
            offset = self.free_base_slot()
            if offset is not None:
                self.data[offset:offset + 18] = dtd
                return "base-dtd"
            offset = self._cta_free_dtd_offset()
            if offset is not None:
                self.data[offset:offset + 18] = dtd
                return "cta-dtd"
        self._add_type1_record(self.encode_type1(ml))
        return "displayid"
