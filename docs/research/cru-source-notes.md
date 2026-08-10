# CRU (ToastyX Custom Resolution Utility) — EDID-writing behavior notes

Interoperability reference for reimplementing CRU's EDID-writing logic in Python
(linux-cru). Derived from reading the CRU C++ Builder source at
`/home/phiality/.claude/jobs/c6462b2a/tmp/cru-src/CRU/`. All behavior is described
in our own words with byte offsets and formulas; no code is copied.

Conventions used below:

- **PClock units**: CRU stores pixel clock internally in units of **10 kHz**
  (`PClock = 14850` means 148.50 MHz). This matches the EDID DTD field unit.
- **VRate/HRate units**: internally **millihertz** for vertical (60000 = 60 Hz)
  and Hz-with-3-decimals for horizontal displayed as kHz (`HRate = 67500` = 67.500 kHz;
  internally `PClock*10000/HTotal`, i.e. mHz-of-kHz).
- "Block" = 128-byte EDID block. `MAX_EDID_BLOCKS = 4`, `MAX_EDID_EXTENSION_BLOCKS = 3`
  (EDIDListClass.h:7-8) — CRU handles a base block plus at most 3 extension blocks.
- `BLANK = -2147483647` is CRU's sentinel for "no value" (ItemClass.h:5).

---

## 1. Detailed timing descriptors (DetailedResolutionClass)

One class handles **two record formats** selected by a `Type` field
(DetailedResolutionClass.cpp:161-229):

- **Type 0** — classic 18-byte EDID DTD (base block, CEA DTD area, VTB-EXT).
- **Type 1** — 20-byte DisplayID **Type I** detailed timing record.

### 1.1 Type 0: 18-byte DTD write layout (DetailedResolutionClass.cpp:341-364)

| Offset | Content |
|---|---|
| 0 | PClock & 0xFF (pixel clock, LE 16-bit, unit 10 kHz) |
| 1 | PClock >> 8 |
| 2 | HActive low 8 bits |
| 3 | HBlank low 8 bits |
| 4 | `((HActive & 0xF00) >> 4) \| ((HBlank & 0xF00) >> 8)` |
| 5 | VActive low 8 bits |
| 6 | VBlank low 8 bits |
| 7 | `((VActive & 0xF00) >> 4) \| ((VBlank & 0xF00) >> 8)` |
| 8 | HFront (HSyncOffset) low 8 bits |
| 9 | HSync width low 8 bits |
| 10 | `((VFront & 15) << 4) \| (VSync & 15)` |
| 11 | `((HFront & 0x300) >> 2) \| ((HSync & 0x300) >> 4) \| ((VFront & 0x30) >> 2) \| ((VSync & 0x30) >> 4)` |
| 12 | `(HActive / 4) & 0xFF` — image size mm, horizontal (see quirk) |
| 13 | `(VActive / 4) & 0xFF` — image size mm, vertical |
| 14 | `(((HActive/4) & 0xF00) >> 4) \| (((VActive/4) & 0xF00) >> 8)` |
| 15 | 0 (H border) |
| 16 | 0 (V border) |
| 17 | `(HPol?2:0) \| (VPol?4:0) \| 8 \| 16 \| Stereo \| (Interlaced?128:0)` |

Key points:

- **Byte 17 always has bits 3 and 4 set** (`8 | 16`): CRU always writes
  "digital separate sync" DTDs. Polarity bit set = **positive** sync.
- **Stereo** is preserved as read: on read, `Stereo = Data[17] & 0x61`
  (bits 0, 5, 6); a value of exactly 1 is normalized to 0
  (DetailedResolutionClass.cpp:254-257). On write it is OR'd back verbatim.
- **Image size quirk**: bytes 12-14 are not physical size — CRU writes
  `HActive/4` × `VActive/4` "mm", i.e. it fakes an aspect-correct size from the
  pixel counts (DetailedResolutionClass.cpp:358-360).
- **Interlaced**: bit 7 of byte 17. The stored `VActive/VFront/VSync/VBlank`
  are **per-field** values (e.g. 1080i is stored as VActive=540). Display code
  doubles them for UI (`VActive*2`, `VFront*2+.5`, `VBlank*2+1`,
  `VTotal*2+1`; DetailedResolutionClass.cpp:680-738). The refresh-rate math
  uses `VTotal*2 + Interlaced` as "half-lines" (see formulas below) — i.e.
  interlaced frames get an extra half line.
- **Read rejects** a descriptor whose first two bytes are both 0 (that's a
  display descriptor, not a timing) (DetailedResolutionClass.cpp:241-242).

### 1.2 Type 1: 20-byte DisplayID Type I record write layout (DetailedResolutionClass.cpp:365-392)

All multi-byte fields little-endian, all counts stored **minus 1**:

| Offset | Content |
|---|---|
| 0-2 | (PClock − 1), 3 bytes LE, unit 10 kHz |
| 3 | flags: bit4 = interlaced, bits 5-6 = stereo (as read), bit7 = "preferred/native" checkbox. Bits 0-3 (aspect ratio) always 0. |
| 4-5 | HActive − 1 |
| 6-7 | HBlank − 1 |
| 8-9 | HFront − 1 (15 bits); **bit 15 = HSync polarity** (1 = positive) |
| 10-11 | HSync width − 1 |
| 12-13 | VActive − 1 |
| 14-15 | VBlank − 1 |
| 16-17 | VFront − 1 (15 bits); **bit 15 = VSync polarity** |
| 18-19 | VSync width − 1 |

`Native` (preferred bit 7 of byte 3) is only available for Type 1
(`NativeAvailable[] = {false, true}`, DetailedResolutionClass.cpp:142).

### 1.3 Validation ranges (DetailedResolutionClass.cpp:108-142)

Two-element arrays indexed by Type `{DTD, DisplayID}`:

| Field | Type 0 (DTD) min..max | Type 1 (DisplayID) min..max |
|---|---|---|
| HActive | 1..4095 | 1..65536 |
| HFront | 1..1023 | 1..32768 |
| HSync | 1..1023 | 1..65536 |
| HBack | 0..4093 | 0..65534 |
| HBlank | 2..4095 | 2..65536 |
| HTotal | 3..8190 | 3..131072 |
| VActive | 1..4095 | 1..65536 |
| VFront | 1..63 | 1..32768 |
| VSync | 1..63 | 1..65536 |
| VBack | 0..4093 | 0..65534 |
| VBlank | 2..4095 | 2..65536 |
| VTotal | 3..8190 | 3..131072 |
| VRate (mHz) | 1..10,000,000 (10 kHz) | same |
| HRate | 1..10,000,000 (10 MHz displayed as 10000 kHz) | same |
| PClock (10 kHz) | 1..65535 (**655.35 MHz limit**) | 1..16,777,216 (167,772.16 MHz) |

Derived-limit helpers additionally constrain combinations, e.g.
max HBack = min(MaxHBack, MaxHBlank − clamp(HFront) − clamp(HSync)); min HBlank =
max(MinHBlank, HFront + HSync + MinHBack); max HTotal = min(MaxHTotal,
HActive + MaxHBlank) (DetailedResolutionClass.cpp:2474-2554).
`IsSupported*` variants always test against the Type-1 (wider) limits so a DTD
that only fits DisplayID is still "supported" but not "valid" for Type 0
(DetailedResolutionClass.cpp:2389-2472).

### 1.4 Timing options

The "Timing" dropdown has 6 entries (DetailedResolutionClass.cpp:12-30):

| # | Name | Algorithm |
|---|---|---|
| 0 | Manual | user-entered fields; only consistency math applied |
| 1 | Automatic — LCD standard | CTA-861 table lookup, else **CVT-RB** |
| 2 | Automatic — LCD native | CTA-861 table at fixed refresh, else CVT-RB computed at 60 Hz then rescaled |
| 3 | Automatic — LCD reduced | CVT-RB, then aggressive blanking reduction (see 1.6) |
| 4 | Automatic — CRT standard | table (empty in source), else **CVT** (normal blanking) |
| 5 | Automatic — Old standard | table (empty), else **GTF** |

There is no CVT-RB2 implementation; "LCD reduced" is CRU's own heuristic on
top of CVT-RB.

**CTA-861 table (`LCDStandard`)** (DetailedResolutionClass.cpp:32-56): rows of
`{HActive, VActive(field), interlaced, minVRate, maxVRate, HFront, HSync,
HBack, VFront, VSync, VBack, HPol, VPol}` covering 3840×2160@60/30,
1920×1080@60/50/30/25/24, 1080i@60/50, 1440×576i@50, 1440×480i@60,
1366×768@60, 1360×768@60, 1280×720@60/50, 720×576@50, 720×480@60,
640×480@60. If HActive/VActive/interlace match and requested VRate falls in
the min/max window, the fixed porches/polarities are used and PClock is
computed from VRate (ceil). The `LCDNative` table is the same set with one
canonical refresh each (e.g. 1366×768 @ 59.789) (DetailedResolutionClass.cpp:58-72).

**Automatic modes run a two-pass snap** (e.g. DetailedResolutionClass.cpp:1226-1236):
compute CVT-RB with the requested rate, compute the actual rate that results
from the rounded pixel clock, recompute CVT-RB using that actual rate, then
restore the requested rate for display. This makes blanking consistent with
the achievable clock.

**Default polarities set by generators**: CVT-RB and both LCD auto modes force
HPol=+, VPol=− ; CVT/CRT and GTF/Old force HPol=−, VPol=+
(DetailedResolutionClass.cpp:1199-1200, 1667-1668, 1692-1693, 1717-1718).

### 1.5 CVT / CVT-RB / GTF formulas as implemented

Internally H period is computed in femtoseconds with integer math. In spec
terms (VRate in Hz, per-frame values; `I` = 1 if interlaced else 0):

- **H period estimate (CVT)**: `Hperiod = (1/VRate − 0.55 ms) / (VActive + VFront + I/2)`
  (DetailedResolutionClass.cpp:1740-1746).
- **H period (CVT-RB)**: `Hperiod = (1/VRate − 0.46 ms) / VActive`
  (DetailedResolutionClass.cpp:1748-1754).
- **H period (GTF)**: same as CVT but with GTF's VFront=1
  (DetailedResolutionClass.cpp:1756-1762).
- GTF/CVT constants: C=40, J=20, K=128, M=600 → C′ = (C−J)·K/256 + J = **30**,
  M′ = M·K/256 = **300** (DetailedResolutionClass.cpp:100-106).
- **CVT HBlank**: `dc = C′ − M′·Hperiod(ms)`, clamped to ≥ 20 (%);
  `HBlank = floor(HActive · dc/(100−dc) / 16) · 16` (truncate to 16-pixel
  multiple) (DetailedResolutionClass.cpp:1812-1825).
- **GTF HBlank**: same duty-cycle formula but **no 20% floor** and rounded to
  the *nearest* 16 (`(x+8)/16*16`) (DetailedResolutionClass.cpp:1827-1840).
- **CVT HSync**: `floor(HTotal/100)·8` i.e. 8% of HTotal truncated to a
  multiple of 8; **GTF HSync**: `round(HTotal/100)·8` (with +50 before the
  divide) (DetailedResolutionClass.cpp:1780-1794).
- **CVT HBack = HBlank/2; HFront = HBlank/2 − HSync** (same for GTF)
  (DetailedResolutionClass.cpp:1764-1810).
- **CVT VFront = 3**; **GTF VFront = 1** (DetailedResolutionClass.cpp:1842-1856).
- **CVT VSync by aspect** (aspect = VActive·4000/HActive, doubled if
  interlaced; matched with ±2% window): 16:9→5, 15:9→7, 16:10→6, 4:3→4,
  5:4→7, anything else → **10** (DetailedResolutionClass.cpp:89-98, 1858-1876).
  **GTF VSync = 3**.
- **CVT VBack**: `floor(0.55 ms / Hperiod) + 1 − VSync`, min **6**
  (DetailedResolutionClass.cpp:1886-1901).
- **CVT-RB**: fixed `HFront=48, HSync=32, HBack=80` (HBlank=160);
  VFront=3; VSync from the same aspect table;
  `VBack = floor(0.46 ms / Hperiod) + 1 − VFront − VSync`, min 6
  (DetailedResolutionClass.cpp:1690-1713, 1903-1918).
- **GTF VBack**: `round(0.55 ms / Hperiod) − 3`, no minimum
  (DetailedResolutionClass.cpp:1920-1935).
- **Pixel clock rounding**:
  - CVT: `PClock = floor(HTotal / Hperiod / 0.25MHz)·0.25MHz` (truncate to
    0.25 MHz) (DetailedResolutionClass.cpp:2089-2106).
  - CVT-RB: `PClock = floor(VRate·HTotal·(VTotal·2+I)/2 / 0.25MHz)·0.25MHz`
    (DetailedResolutionClass.cpp:2108-2125).
  - GTF: `PClock = round(VRate·HTotal·(VTotal·2+I)/2)` to nearest 10 kHz
    (DetailedResolutionClass.cpp:2127-2144).
  - From VRate (manual/table modes): **ceiling** to 10 kHz:
    `PClock = ceil(VRate·HTotal·(VTotal·2+I) / 2e7)` with VRate in mHz
    (DetailedResolutionClass.cpp:2146-2163).
  - From HRate: `PClock = ceil(HRate·HTotal / 1e4)` (DetailedResolutionClass.cpp:2165-2182).
- **Actual rates back-computed**: `ActualVRate = PClock·2e7 / HTotal / (VTotal·2+I)`
  (mHz), `ActualHRate = PClock·1e4 / HTotal` (DetailedResolutionClass.cpp:2184-2220).

### 1.6 "LCD reduced" heuristic (DetailedResolutionClass.cpp:1294-1563)

Start from CVT-RB (two-pass). Then, only when requested VRate > 60.5 Hz:

- If `HActive·VActive > 2,457,600` (larger than ~1920×1280):
  1. While PClock > 330 MHz: shrink VBack while VBlank>15, else HBack in steps
     of 8 down to 48 (recomputing CVT-RB VBack), else shave the largest of
     VFront/VSync/VBack down to 3.
  2. If still > 330 MHz: redo two-pass CVT-RB; then while PClock > 400 MHz:
     shrink VBack while VBlank>21, else HBack down to 56.
  3. Repeat for a 404 MHz threshold.
  4. If still over 404 MHz: fall back to fixed micro-blanking —
     for area > 3,686,400 (~>2560×1440): `HF/HS/HB = 48/32/48`, `VF/VS/VB=3/3/3`;
     if still > 540 MHz: `16/24/24`, `3/3/3`, both polarities positive.
     For smaller areas: `48/32/64`, `2/2/2`; if > 540 MHz: `4/16/2`,
     `VF/VS/VB = 1/1/7`, both polarities positive.
- Else (small modes) if PClock > 165 MHz: set `HF/HS/HB = 32/40/48`, CVT
  VFront/VSync, VBack=VSync (1920×1080 also flips both polarities positive),
  then iteratively shave H porches by 8 / V porches by 1 (largest first,
  floors of 8 and 3) until ≤ 165 MHz; final fallback `24/32/32` with
  `VBack = 2·VSync`.

The magic thresholds in 10 kHz units: 16500 (165 MHz single-link DVI/HDMI 1.x),
33000 (330 MHz dual-link/HDMI 1.4), 40000/40400, 54000 (540 MHz).

### 1.7 Read-time rate snapping (Init, DetailedResolutionClass.cpp:292-331)

After reading a descriptor, CRU derives ActualVRate then tries to present a
"nice" rate: round to nearest 1 Hz; if recomputed PClock doesn't reproduce the
stored one, try NTSC rates (`VRate·1000/1001` when the rounded rate is a
multiple of 24 or 30 Hz); then try rounding to 0.1 Hz; else keep the exact
actual rate. Purely cosmetic — the stored PClock is never changed by this.

### 1.8 Interlaced toggling helpers

`SetInterlaced` swaps the V-fields with a shadow copy so toggling is lossless
(DetailedResolutionClass.cpp:962-996). `UpdateInterlaced` computes the shadow:
special-cases 1080p (VActive 1080, porches 4/5/36) ↔ 1080i (540, 2/5/15);
otherwise halves/doubles VActive using heuristics (aspect ratio > 51/125, or
1440/2880-wide SD modes with VActive 472-488/566-586)
(DetailedResolutionClass.cpp:1080-1129). Non-interlaced rates < 45 Hz double
when switching to interlaced (DetailedResolutionClass.cpp:1131-1141).

---

## 2. DisplayID extension block (DIDDataListClass, DIDDetailedResolutionListClass)

### 2.1 Which DisplayID CRU writes

CRU writes a **DisplayID 1.2 section** inside an EDID extension block. There
is no DisplayID 2.0 (Type VII) writing; the only structure version emitted is
`0x12` (ExtensionBlockClass.cpp:331-336). Timing records are 20-byte
**Type I** (tag 0x03) — see §1.2 for layout. (DisplayID 2.0 Type VII happens
to share the 20-byte layout, but CRU tags the block 0x03 and the section 0x12,
so it is a 1.2/1.3-style extension.)

### 2.2 Extension block layout as written (ExtensionBlockClass.cpp:331-336, 726-758)

| Byte | Value |
|---|---|
| 0 | 0x70 — EDID extension tag "DisplayID" |
| 1 | 0x12 — DisplayID structure version 1.2 |
| 2 | section payload size (bytes of data blocks, ≤ **121**) |
| 3 | 0 — product type identifier ("extension section") |
| 4 | 0 — extension count |
| 5 .. 5+size−1 | DisplayID data blocks, packed |
| 5+size | **section checksum**: byte such that sum of bytes 1..(5+size) ≡ 0 mod 256 (tag byte 0 excluded) |
| ... | zero fill |
| 127 | **EDID block checksum** (recomputed at save/export; see §3.3) |

Section checksum implementation: starts summing at index 1 and stores the
negated sum at `data[5+size]` (ExtensionBlockClass.cpp:748-758). So a
DisplayID-in-EDID block carries **two checksums**: the DisplayID section
checksum and the standard EDID block checksum at byte 127.

Max payload is 121 = 128 − 5 (tag + 4-byte section header) − 1 (section
checksum) − 1 (block checksum) (ExtensionBlockClass.cpp:427-430, 763).

### 2.3 DisplayID data blocks

Each data block: `[tag][revision=0][payload_len][payload...]`
(3-byte header; DIDDataListClass.cpp:54-68). CRU recognizes tags 0-19 and 127
for display purposes (DIDDataListClass.cpp:111-181; tag 3 = "Detailed
resolutions", 18 = Tiled display topology needing size ≥ 22, 127 =
vendor-specific). **It can only create/edit two kinds**
(DIDDataListClass.cpp:345-362, AddDIDDataClass.cpp:7-10):

1. **Type I detailed resolutions block (tag 0x03)**
   (DIDDetailedResolutionListClass.cpp:53-66): writes
   `[0x03][0x00][n*20]` followed by up to **5** 20-byte Type I records
   (default slot limit 5; DIDDetailedResolutionListClass.h:11). On read, any
   record that fails parsing is silently dropped and the rest compacted.
2. **Tiled display topology (tag 0x12, 22-byte payload)**
   (TiledDisplayTopologyClass Write): `[18][0][22]`, then
   byte 3 = `(enclosures<<7) | (multiTileBehavior<<3) | singleTileBehavior | (pixelMultiplier?64:0)`;
   byte 4 = `((HTiles−1)&15)<<4 | (VTiles−1)&15`; byte 5 same for H/V location;
   byte 6 packs the high 2 bits of each of the four counts;
   bytes 7-10 = (HSize−1), (VSize−1) LE; byte 11 pixel multiplier;
   bytes 12-15 top/bottom/right/left bezel; bytes 16-20 vendor/product ID
   (3 ASCII chars + packed hex product code, note **byte 19 = low product
   digits, byte 20 = high digits** — same nibble packing as the EDID header
   product ID); bytes 21-24 = 32-bit serial LE.

Everything else read from an existing DisplayID extension is preserved as an
opaque slot and re-emitted byte-for-byte (order preserved, moved to the front
of the section as blocks are packed contiguously). Reading tolerates truncated
blocks by clamping the declared payload length to the space remaining
(DIDDataListClass.cpp:59-60); zero tag + zero length entries are skipped.

### 2.4 When does CRU choose DisplayID over a DTD?

**Never automatically.** The user chooses where a resolution lives: base-block
DTD slots (4), CEA extension DTD area, or a DisplayID extension's detailed
resolution block. The only functional advantages encoded in the code are the
wider Type-1 limits (§1.3: >4095 pixels, >655.35 MHz) and the per-record
"native/preferred" bit. A Python reimplementation should mirror this: place
timings where the caller says, but validate against the per-container limits.

---

## 3. Extension block management (ExtensionBlockClass / ExtensionBlockListClass / DisplayClass)

### 3.1 Block types CRU can edit

`TypeCode[] = {0x02 (CEA-861), 0x10 (VTB-EXT), 0x70 (DisplayID), 0x00 (Default)}`
(ExtensionBlockClass.cpp:20-26). Other tags (0x20 EDID 2.0, 0x40 DI-EXT, 0x50
LS-EXT, 0x60 DPVL, 0xF0 block map) are recognized for labeling only
(ExtensionBlockClass.cpp:128-183) and passed through **unmodified**: for an
unrecognized type, `ExtensionWrite` does nothing so the bytes read are the
bytes written (ExtensionBlockClass.cpp:303-344). A brand-new block defaults to
a CEA-861 block with header `02 03 04` (ExtensionBlockClass.cpp:30-47).

### 3.2 Rewrites per type (ExtensionBlockClass.cpp:303-344)

Every editable block is fully regenerated (zeroed then rebuilt):

- **CEA-861 (0x02)**: byte0=0x02, byte1=**0x03** (always revision 3).
  Data blocks first at offset 4 (CEADataWrite, ExtensionBlockClass.cpp:608-626),
  byte2 (DTD offset "d") = 4 + total data-block size; then 18-byte DTDs
  immediately after (DetailedWrite, ExtensionBlockClass.cpp:510-531). Byte3
  flags: bit6 (0x40) set if any audio or speaker block present; bit7 (0x80)
  set if a video capability block declares underscan (bits 3:2 == 10 binary,
  CEADataListClass.cpp:441-458); bits 4/5 = YCbCr 4:2:2 / 4:4:4 (written only
  when an HDMI VSDB is present, ExtensionBlockClass.cpp:313-315;
  ColorFormatListClass Write masks byte3 with 0xCF then ORs 16/32).
  **The low nibble of byte 3 (native DTD count) is always 0.**
  Capacity: 123 payload bytes shared between data blocks and DTDs
  (ExtensionBlockClass.cpp:415-419); DTD list limited to 6 slots
  (ExtensionBlockClass.cpp:43).
- **VTB-EXT (0x10)**: byte0=0x10, byte1=**0x01**; byte2 = number of DTDs,
  DTDs at offset 5; byte4 = number of 2-byte standard timings placed right
  after the DTDs; byte3 (CVT count) left 0 — CRU never writes VTB CVT codes
  but accounts for them when reading offsets
  (ExtensionBlockClass.cpp:324-329, 643-691). Capacity 122 bytes.
- **DisplayID (0x70)**: see §2.2. Capacity 121 bytes.
- **Default (0x00)**: 128 zero bytes (placeholder that still occupies a block).

### 3.3 Extension count byte (126) and checksums

- The extension list serializes as `[count at offset 126][unused 127][blocks…]`:
  `ExtensionBlockListClass::Write` writes `Data[0] = SlotCount` where Data
  points at base-block offset 126 (ExtensionBlockListClass.cpp:44-56;
  DisplayClass.cpp:1320-1326). So **byte 126 is always the literal number of
  stored extension blocks**; removing/adding blocks in the UI reorders the
  packed array and updates the count. Blocks are reordered with simple slot
  exchanges (Up/Down buttons; ListClass Exchange).
- **No block map (0xF0) is ever created**, even with 2-3 extensions. An
  existing block map would be kept as an opaque block (and counted).
- **Checksums**: while editing, byte 127 of every block is kept **zero**
  (`ClearChecksums`, DisplayClass.cpp:1333-1341, called from DisplayWrite).
  Real checksums are computed only on Save/Export:
  `checksum = −(sum of bytes 0..126) mod 256` per 128-byte block for **all 4
  block slots**, including opaque/passthrough blocks — so corrupt input
  checksums get silently repaired (`CalculateChecksums`,
  DisplayClass.cpp:1343-1357).
- **Blank CEA stub**: when the user removed all extensions but the original
  hardware EDID had extensions, CRU appends one "blank" CEA block so the
  override doesn't shrink the block count seen by drivers:
  bytes `02 03 00 … 00` with checksum 0xFB at 127, and increments byte 126
  (with `Data[127]--` compensation in the INF path)
  (ExtensionBlockListClass.cpp:58-71; DisplayClass.cpp:791-797, 957-962).
- **Read-side normalization** (`FixData`, DisplayClass.cpp:374-418): rejects
  data without the 8-byte EDID header; zeroes bytes 12-15 (serial, rewritten
  later from properties); if version isn't 1.4 exactly, forces version bytes
  to 1.3 and, for digital displays, resets byte 20 to plain 0x80 (dropping
  1.4 interface/bit-depth bits); always sets **byte 24 bit 1** (preferred
  timing mode applies to first DTD). If extension count is 1 but block 1 is
  the blank CEA stub (override case) or all-0xFF (active case), count is
  reset to 0.

### 3.4 Base block resolution areas (DisplayClass)

- 4 DTD slots at offset 54 (72 bytes). Before writing, all 4 slots are
  pre-filled with **dummy descriptors** `00 00 00 10 00…` (tag 0x10)
  (DisplayClass.cpp:1221-1235); DTDs are then written from the first slot.
- Descriptor priority for the remaining slots — properties are written in this
  order into free dummy slots: **range limits, then name (0xFC), then serial
  number (0xFF)** (PropertiesClass::Write, PropertiesClass.cpp:291-299).
- Standard timings: 8 slots at offset 38 plus CRU's trick of storing **extra
  standard timings inside spare DTD slots**: slot budget is
  `(4 − DTD count)·6 + 8` (DisplayClass.cpp:1271-1275) — i.e. an unused
  18-byte descriptor holds 6 more 2-byte standard timings. Standard timing
  encoding: byte0 = (Width−248)/8; byte1 = aspect<<6 | (rate−60), aspect
  codes 0..3 = 16:10, 4:3, 5:4, 16:9 (StandardResolutionClass.cpp).
  (The reverse: DTD count is capped by `4 − (StdCount−3)/6`,
  DisplayClass.cpp:1242-1246.)
- Established timings: 3 masked bit-bytes at offset 35.

---

## 4. CTA-861 data blocks (CEADataListClass and friends)

### 4.1 Parsing/serialization

Data blocks are `[type:3bits | length:5bits][payload…]`
(CEADataListClass.cpp:54-68). Truncated lengths are clamped; `00` bytes are
skipped. Blocks the UI can't edit are preserved verbatim and re-packed in list
order. Slot storage is 32 bytes/block, up to 123 slots.

Type detection (CEADataListClass.cpp:98-202): tag 1 audio; tag 2 video (SVD);
tag 3 vendor-specific, sub-classified by OUI —
`03 0C 00` (LE bytes 1-3) → HDMI VSDB; `D8 5D C4` → HDMI Forum VSDB;
`1A 00 00 01 01` at bytes 1-5 **plus** byte6 ≠ 0, byte7 ≠ 0, byte6 ≤ byte7 →
**FreeSync (AMD OUI 0x00001A)**; tag 4 speaker allocation (needs length ≥ 3);
tag 7 extended with byte1 = 0 video capability, 1 vendor video, 4 HDMI video,
5 colorimetry, 6/7 HDR static/dynamic, 13 video preference, 14 YCC420 video,
15 YCC420 capability map, 17 vendor audio, 18 HDMI audio, 19 room config,
20 speaker location.

Blocks CRU can create (AddCEADataClass.cpp:7-10 with min sizes
{1,1,4,6,8,9,4,3}): TV resolutions (SVD), audio formats, speaker setup,
HDMI support, HDMI 2.0 support, FreeSync range, colorimetry, video capability.
Editable additionally: YCC420 video list.

### 4.2 SVDs / TV resolutions (TVResolutionClass, TVResolutionListClass)

- SVD byte: `code | (native ? 0x80 : 0)`; the native bit is only permitted for
  VIC 1..64 (`NativePossible`, TVResolutionClass.cpp:544-558). For codes >127
  the whole byte is the VIC (7-bit-native logic skipped on read via
  NativePossible check, TVResolutionClass.cpp:363-396).
- The same list class writes either a plain video data block
  (`[2<<5 | n][SVDs…]`) or a **YCC420 video data block**
  (`[7<<5 | n+1][14][SVDs…]`) depending on a flag
  (TVResolutionListClass Write).
- CRU knows VICs 1-127 and 193-219 with resolution/aspect/rate tables
  (TVResolutionClass.cpp:130-352); `Codes[code][5]` marks which VICs CRU
  considers "supported" (drives UI display only, not writing).
- **Native format count** (CEA header byte 3 low nibble) is never written
  (always 0) — nativeness is expressed only via SVD bit 7.

### 4.3 HDMI VSDB (HDMISupportClass.cpp:308-414)

Write layout (offsets within the data block, byte 0 is the header):

| Offset | Content |
|---|---|
| 0 | `(3<<5) \| length` (length computed from content, min 5) |
| 1-3 | OUI `03 0C 00` |
| 4-5 | physical address, two BCD-nibble bytes (parsed from "a.b.c.d", each 0-15) |
| 6 | deep color/etc flags: bit0 DVI dual link, bit3 DC_Y444, bit4 30-bit, bit5 36-bit, bit6 48-bit, bit7 Supports_AI — byte only present if any flag set |
| 7 | **Max_TMDS_Clock in 5 MHz units** (`TMDSClock/5`); present only when "override" checked; UI range 5..1275 MHz step 5 (HDMISupportClass.cpp:9-11) |
| 8 | content types bits 0-3 (graphics/photo/cinema/game), bit7 latency present, bit6 interlaced latency present, bit5 "HDMI_Video_present" (set when VIC/3D content follows) |
| 9-10 | video/audio latency, encoded `ms/2 + 1`, 255 = unsupported; only when latency flag set (latency valid range 0..500 ms, even) |
| 11-12 | interlaced video/audio latency, same encoding |
| next | HDMI-VIC/3D flags byte (`OtherFlags`, e.g. 3D_present bits) when any of flags/VICs/3D data exist |
| next | `HDMI_VIC_LEN<<5 \| HDMI_3D_LEN` |
| next | HDMI-VIC codes, 1 byte each (codes 1-4 = 4K@30/25/24 and 4096×2160@24, HDMIResolutionClass.cpp:54-61) |
| next | opaque 3D/extra data preserved from read (`OtherData`, ≤ 23 bytes) |

Reading tolerates short blocks (each optional byte defaults off), clamps
VIC/3D lengths to the remaining size, and treats latency values outside 0..500
as "supported flag false / value blank" (HDMISupportClass.cpp:118-306).

### 4.4 HDMI Forum VSDB (HDMI2SupportClass.cpp:60-80)

Fixed 7-byte payload written:

| Offset | Content |
|---|---|
| 0 | `(3<<5) \| 7` |
| 1-3 | OUI `D8 5D C4` |
| 4 | version (kept from read, default 1) |
| 5 | **Max_TMDS_Character_Rate in 5 MHz units** (0 = blank/not indicated), UI range 5..1275 step 5 |
| 6 | feature flags masked with **0xCF** (bits 0-3 and 6-7 preserved: 3D_OSD_Disparity, Dual_View, Independent_View, LTE_340Mcsc_scramble, RR_Capable, SCDC_Present; bits 4-5 dropped) |
| 7 | deep-color 4:2:0 flags masked with 0x07 |

**No FRL support**: bytes 8+ (FRL rates, DSC, VRR min/max, ALLM…) are not
modeled — a longer HF-VSDB read from hardware would be truncated to 7 payload
bytes on rewrite (Read only consumes bytes ≤ 7; Write always emits length 7).
This is a CRU limitation to be aware of / improve on.

### 4.5 Other CEA blocks CRU writes

- **Speaker allocation** (tag 4): 3 payload bytes, CRU writes its bitfield +
  zero padding (SpeakerSetupClass Write).
- **Audio format** (tag 1): 3 bytes per format —
  `byte0 = format<<3 | (channels−1)`, `byte1 = sample-rate bits`,
  `byte2` = bit-depth bits for LPCM (format 1), max-bitrate/8kbps for formats
  2-8, else raw flags (AudioFormatClass Write).
- **Video capability** (extended tag 0): `[7<<5 | 2][0][flags]`
  (VideoCapabilityClass Write). Underscan handling described in §3.2.
- **Colorimetry** (extended tag 5): `[7<<5 | 3][5][byte2][byte3]`,
  byte2 unmasked, byte3 masked 0x8F (colorimetry bit 8 = DCI-P3 in bit7 of
  byte3, metadata bits 0-3) (ColorimetryClass.cpp:8-13, 180-196).

---

## 5. FreeSync range block (FreeSyncRangeClass.cpp:53-73)

A CEA **vendor-specific data block (tag 3)** with AMD's OUI, written as a
fixed 8-byte payload:

| Offset | Content |
|---|---|
| 0 | `(3<<5) \| 8` |
| 1-3 | OUI `1A 00 00` (0x00001A little-endian) |
| 4 | 0x01 |
| 5 | 0x01 |
| 6 | **minimum refresh in Hz** (1 byte) |
| 7 | **maximum refresh in Hz** (1 byte) |
| 8 | 0 |

Valid range 1..255 Hz, min ≤ max enforced (FreeSyncRangeClass.cpp:8-9,
97-122). Bytes 4-5 (`01 01`) are treated as a fixed signature; detection
additionally requires nonzero min/max with min ≤ max
(CEADataListClass.cpp:129-130). Anything after byte 8 in a longer AMD block
is dropped on rewrite (write emits exactly length 8).

---

## 6. Monitor range limits descriptor (tag 0xFD) (PropertiesClass)

### 6.1 Write layout (PropertiesClass.cpp:397-454)

Written into the first free dummy descriptor slot (see §3.4 priority):

| Descriptor offset | Content |
|---|---|
| 0-2 | 00 00 00 |
| 3 | 0xFD |
| 4 | offset flags: bit0 min-V +255, bit1 max-V +255, bit2 min-H +255, bit3 max-H +255 (EDID 1.4 "range limit offsets") |
| 5 | min V rate: `(v−1) % 255 + 1` (i.e. stored value 1-255, flag adds 255) |
| 6 | max V rate, same encoding |
| 7 | min H rate (kHz), same |
| 8 | max H rate, same |
| 9 | max pixel clock / 10 (MHz/10; valid 10..2550 MHz in steps of 10) |
| 10 | timing-support byte: **1** ("range limits only") if EDID ≥ 1.4, else **0** (default GTF) |
| 11 | 0x0A |
| 12-17 | 0x20 padding |

When byte 10 is written as 1 (1.4 path), CRU also sets **base byte 24 bit 0**
(continuous frequency); it clears that bit first in all cases
(PropertiesClass.cpp:402, 413-414).

`ExtRange` is 255 when the (already normalized) EDID version is ≥ 1.4, else 0
(PropertiesClass.cpp:211-214); the valid V/H maxima become 255+ExtRange, i.e.
**510 max with 1.4 offsets, 255 otherwise** (PropertiesClass.cpp:16-19,
1159-1201).

### 6.2 Read behavior (PropertiesClass.cpp:201-281)

Reads the four rates applying the byte-4 offset flags, MaxPClock = byte9·10.
If out-of-range, falls back to previously stored values, else blanks. The
"include" checkbox auto-enables on read of the hardware EDID; for override
EDIDs it enables when the descriptor looks CRU/1.4-shaped:
`ExtRange && (byte24 & 1) && minH == maxH && byte10 == 1`.

### 6.3 Does CRU auto-extend range limits for new modes?

**No.** Nothing in the source recomputes min/max V/H or max pixel clock from
the resolution lists. Range limits are purely user-edited properties, only
constrained by the validity ranges above, and are dropped (not written) when
no dummy descriptor slot remains (`FixIncludes` drops includes when slots run
out, preferring range limits > name > serial; PropertiesClass.cpp:828-869).
A Python reimplementation that wants "safe" behavior must add its own
auto-extend logic — CRU relies on the descriptor simply being absent or the
user updating it.

---

## 7. Export / import (DisplayClass.cpp:420-897)

### 7.1 Export

Before any export: regenerate all areas, write properties, then
`CalculateChecksums()` (DisplayClass.cpp:714-735). Block count exported =
`(index of highest extension block whose first byte ≠ 0) + 1`
(DisplayClass.cpp:747-751).

- **.bin (FileType 1)**: **raw EDID bytes, `count × 128` bytes, no padding,
  no header** (DisplayClass.cpp:755-761). Trailing all-zero ("Default")
  blocks are dropped because their first byte is 0.
- **.dat (2)**: text hex dump with a 3-line header (`EDID BYTES:`, column
  ruler, dashes), rows `0xNN0 | xx xx …` 16 bytes/row.
- **.inf (3)**: Windows monitor INF with
  `HKR,EDID_OVERRIDE,"<block>",0x01,0x00,0xFF,…` lines (one per non-zero
  block; zero-leading blocks skipped). Before writing, if extension count is 0
  but the original EDID had extensions, the blank CEA stub is appended
  (count byte incremented and checksum byte decremented by 1 to keep block 0
  valid) (DisplayClass.cpp:790-797).
- **.txt (4)**: plain hex, 16 bytes per line, space-separated, no header.

### 7.2 Import (DisplayClass.cpp:420-702)

Tries a sequence of format templates against the file, first match wins:

1. raw binary (16-byte groups),
2. plain hex text (`%2x` pairs),
3. 3 header lines then `offset string 16×hex` rows (its own .dat format —
   the offset is validated: `hex % 128 == index`, block number monotonic),
4. 3 header lines then `str str 10×hex` rows (an alternate dump layout),
5. hex-with-separator (`%x` then one separator char, e.g. comma lists),
6. Windows INF (`HKR … EDID_OVERRIDE, <block>, 1, <128 hex bytes>` — must be
   exactly 128 values, block 0..3).

Behavioral notes:

- Block 0 must pass the 8-byte header check (`00 FF FF FF FF FF FF 00`) or
  that candidate format fails (DisplayClass.cpp:539-543).
- **"Complete" vs default import**: complete replaces the whole base block;
  default keeps bytes 0-34 (header, vendor/product, serial, version, display
  parameters, chromaticity is 25-34… precisely bytes 0-34) of the current
  override and copies only bytes **35-127** from the file
  (DisplayClass.cpp:546-549). Extension blocks are always replaced.
- Extension count is forced to 0 before import and re-derived: if the file
  provides extension blocks without a valid block 0, byte 126 is raised to
  the highest block index seen (DisplayClass.cpp:428, 567-568).
- **Checksums in imported files are ignored entirely** — never verified; they
  get recomputed on save/export.
- After import, `FixData` normalization runs (version forcing, byte-24 bit 1,
  stub-extension collapse; §3.3).

### 7.3 Registry persistence (Windows-specific, for reference)

Override EDID written to
`HKLM\SYSTEM\CurrentControlSet\Enum\DISPLAY\<id>\<dev>\Device Parameters\EDID_OVERRIDE`
values "0".."3", one 128-byte REG_BINARY per block; unused block values are
deleted (DisplayClass.cpp:945-1002). CRU also stashes its own metadata beside
it (`CRU_Name`, `CRU_Serial_Number`, `CRU_Range_Limits` — 16-bit BE pairs +
include flag, `CRU_Extensions` — original extension count)
(PropertiesClass.cpp:24-27, 456-702).

---

## 8. Assorted reimplementer notes / magic numbers

- **Capacities**: base block: 4 DTDs, 8+6·(free DTD slots) standard timings;
  CEA: 123 bytes shared (6 DTD slots max); VTB: 122 bytes; DisplayID:
  121 bytes, ≤ 40 data blocks, ≤ 5 Type I timings per resolutions block.
- **Ordering requirements baked into the writers**: CEA data blocks always
  precede DTDs and byte 2 = 4 + datablock bytes; DisplayID data blocks are
  packed contiguously from offset 5; VTB order is DTDs then standard timings.
  List order within each area is user-controlled (Exchange up/down) and
  otherwise preserved from read.
- **Descriptor slot recycling**: unused base-block descriptor slots are dummy
  `00 00 00 10 00…`; property descriptors are only placed into slots whose
  tag is 0x10 at write time — so DTDs always win slots over name/serial/range
  limits.
- **Deleting the last HDMI VSDB clears YCbCr flags** in the CEA header
  (ExtensionBlockFormClass.cpp:1214-1224), since color formats are only
  written when an HDMI block exists.
- **Default new-block contents**: new HDMI VSDB defaults to physical address
  0.0.0.0, TMDS override 340 MHz; new HF-VSDB defaults to version 1,
  600 MHz character rate, no features; new colorimetry block defaults to
  bytes `03 01` (xvYCC601+709, MD0); FreeSync defaults blank min/max.
- **TV "native" default heuristic** (`CalculateNative`,
  DetailedResolutionClass.cpp:1143-1192): CRU derives the display's native
  mode from the first base-block DTD, matching the LCDNative table; unmatched
  modes get 60 Hz via LCD-standard (digital or HSync ≤ 48) or CRT-standard
  timing with PClock truncated to 0.25 MHz. Used to seed HDMI/DID dialogs,
  not written anywhere by itself.
- **Default_Monitor pseudo-display**: when no hardware EDID exists CRU
  fabricates a base block starting
  `00 FF…00, mfr 0x41D0 ("PNP"), product 0x09FF`, version 1.3, digital,
  standard sRGB-ish chromaticity bytes (DisplayClass.cpp:330-364), i.e.
  PNP09FF "Default Monitor".
- **Product/vendor ID codec**: 3-letter PNP ID packed 5 bits/letter into
  bytes 8-9 (big-endian bit order); product code stored as 4 hex digits with
  **byte 10 = low two digits, byte 11 = high two digits** — CRU's 7-char
  "ABC1234" maps digits 1,2 → byte 11 and 3,4 → byte 10
  (PropertiesClass.cpp:115-130, 301-315). Serial = 32-bit LE at 12-15.
- Name (0xFC) and serial number (0xFF) descriptors: 13 chars max, terminated
  with 0x0A when short, padded with 0x20 (PropertiesClass.cpp:329-395).

---

## 9. Delta vs published specs

Places where CRU deviates from or extends VESA/CTA/AMD documents — a Python
writer aiming for byte-compatibility must reproduce these:

1. **DTD image size fields are fake**: bytes 12-14 hold `HActive/4 × VActive/4`
   instead of physical millimetres (§1.1). Borders always 0.
2. **Sync type is always "digital separate"** (byte 17 bits 3-4 set); CRU
   cannot write composite/analog sync DTDs, and any read DTD is rewritten in
   this form (only polarity, stereo, interlace survive).
3. **CEA revision is always 3** and the native-format count nibble of byte 3
   is always 0, even when SVDs carry native bits.
4. **DisplayID version written is 1.2** (`0x12`) with revision-0 data blocks;
   Type I timing byte-3 aspect-ratio bits are always 0 ("1:1") regardless of
   the actual aspect — readers that honor those bits will see a wrong aspect.
5. **No block map (0xF0)** even with 2-3 extension blocks; EDID 1.3 formally
   requires one for >1 extension (1.4 made it optional). CRU also normalizes
   version to 1.3 unless the source was exactly 1.4 — so it happily writes a
   "1.3" EDID with 2+ extensions and no map.
6. **Checksum repair**: block checksums are never validated on read and
   always recomputed for all blocks on save/export, including untouched
   opaque blocks — invalid checksums in, valid checksums out.
7. **HF-VSDB truncated to 7 payload bytes**: FRL, DSC, VRR-range and other
   HDMI 2.1 fields are silently dropped when a monitor's HF-VSDB is rewritten;
   feature byte 6 is masked to 0xCF and byte 7 to 0x07.
8. **CVT rounding details**: CVT HBlank truncates to 16-pixel multiples where
   the spec's cell-based math rounds differently in edge cases; GTF HBlank
   rounds to nearest 16; CVT pixel clock truncates to 0.25 MHz, GTF rounds to
   10 kHz, manual/table clocks are **ceiled** to 10 kHz. CVT VSync uses a ±2%
   aspect window and falls back to 10 lines for nonstandard aspects.
9. **"LCD reduced" is proprietary**: CVT-RB followed by the 165/330/400/404/540
   MHz blanking-shrink cascade (§1.6). Not CVT-RB2 (no 1000/1001 flag, no
   8-line VBlank floor, no 80-pixel HBlank).
10. **FreeSync block**: AMD's VSDB is written as an 8-byte payload with fixed
    `01 01` at offsets 4-5 and single-byte Hz min/max at 6-7; CRU both
    requires and produces min ≤ max, nonzero. Longer AMD blocks (v2 fields)
    are truncated on rewrite.
11. **Range-limits descriptor**: for EDID <1.4 CRU writes byte 10 = 0
    ("default GTF") even for displays that don't support GTF; for 1.4 it
    always uses "range limits only" (1) + continuous-frequency bit. It never
    writes CVT-support (4) or secondary-GTF (2) forms, and never auto-extends
    limits to cover added modes.
12. **EDID version forcing**: any EDID that isn't 1.4 becomes 1.3 on load
    (byte 19 forced to 3), byte 20 collapsed to 0x80 for digital, byte 24
    bit 1 force-set, serial bytes rewritten from properties. Bytes 12-15 are
    zeroed then repopulated, so week/year (16-17) survive but serial edits
    ride through PropertiesClass only.
13. **VTB-EXT**: CRU reads CVT-code counts but can only write DTDs and
    2-byte standard timings; a VTB block with CVT codes loses them on rewrite
    (byte 3 written as 0).
