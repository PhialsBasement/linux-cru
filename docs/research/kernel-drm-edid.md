# Kernel-Level (DRM/KMS) Custom Resolution & Refresh Rate Forcing on Linux — Technical Report

**Scope:** the compositor-agnostic layer — everything below the Wayland compositor / X server. Verified against kernel 6.x–master sources (drm_modes.c, drm_edid.c, drm_debugfs.c, drm_sysfs.c, drm_probe_helper.c, amdgpu_dm_debugfs.c), kernel admin docs, and current community sources (August 2026). Local verification performed on a live 2026-era kernel (CachyOS, amdgpu, connectors `card1-DP-1`, `card1-DP-2`, `card1-HDMI-A-1`, `card1-HDMI-A-2`).

**Executive summary for the CRU project:** There are exactly two kernel-level mechanisms, and they are complementary:

1. **`video=` cmdline** — injects a *CVT/GTF-computed* mode into the connector's mode list. Cannot express arbitrary modelines. Good for quick standard-timing additions; insufficient for a real CRU.
2. **EDID override** — replaces the monitor's EDID entirely, either persistently (`drm.edid_firmware=` + initramfs) or at runtime (debugfs `edid_override` + forced reprobe). This is the CRU-equivalent path: whatever timings the EDID advertises (DTDs, CTA-861, DisplayID Type VII) become the connector's mode list, visible identically to every Wayland compositor and Xorg. **This is the universal mechanism your utility should build on.** Since kernel ~6.1, both paths converge on the same code (`drm_edid_override_get()`), with full validation (`drm_edid_valid()`: header + per-block checksums).

Neither mechanism bypasses driver link/bandwidth validation (`mode_valid` + `drm_mode_prune_invalid()` run on cmdline and override modes alike) — same as CRU on Windows.

---

## 1. `video=` kernel command line parameter

### 1.1 Syntax

Documented in [Documentation/fb/modedb.rst](https://www.kernel.org/doc/html/latest/fb/modedb.html); parsed for DRM by `drm_mode_parse_command_line_for_connector()` in [drivers/gpu/drm/drm_modes.c](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/drm_modes.c):

```
video=<connector>:<xres>x<yres>[M][R][-<bpp>][@<refresh>][i][m][eDd]
video=<connector>:<name>[-<bpp>][@<refresh>]        # named modes
video=<connector>:e                                  # flags only (no mode)
```

Multiple `video=` parameters may be given, one per connector. Optional comma-separated options after the mode (all verified in current `drm_modes.c`):

```
video=DP-1:2560x1440M@120e,rotate=180,reflect_x
```

Supported options: `rotate=<0|90|180|270>`, `reflect_x`, `reflect_y`, `margin_right/left/top/bottom=<px>`, `panel_orientation=<normal|upside_down|left_side_up|right_side_up>`, `tv_mode=<...>` (kernel ≥ 6.3, for analog TV out). Named modes: `NTSC`, `NTSC-J`, `PAL`, `PAL-M`.

### 1.2 Flags and timing formula

| Flag | Meaning |
|---|---|
| `M` | Compute timings with **VESA CVT** (`drm_cvt_mode()`) instead of GTF |
| `R` | **Reduced blanking** (CVT-RB); only effective **together with `M`** in the DRM path — the `rb` flag is only consumed in the `cmd->cvt` branch |
| `i` | Interlaced |
| `m` | Add margins (1.8% of xres/yres) |
| `e` | **Force connector enabled** — overrides detection (sets `DRM_FORCE_ON`); usable alone: `video=DP-1:e` |
| `D` | Force enabled, digital signal (`DRM_FORCE_ON_DIGITAL`) — matters only for connectors that can be both (DVI-I, TV-out) |
| `d` | Force connector **disabled** (`DRM_FORCE_OFF`) |

**Timing formula (verified in `drm_mode_create_from_cmdline_mode()`, current master):**

```c
if (strlen(cmd->name))
        mode = drm_named_mode(dev, cmd);
else if (cmd->cvt)
        mode = drm_cvt_mode(dev, ...);
else
        mode = drm_gtf_mode(dev, cmd->xres, cmd->yres,
                            cmd->refresh_specified ? cmd->refresh : 60, ...);
```

- **Default is GTF**, not CVT. `M` selects CVT, `MR` selects CVT-RB (what you want for any modern LCD — GTF blanking often exceeds link budget and many monitors won't sync to it).
- **Refresh is an integer.** No fractional rates (59.94, 239.76) can be expressed.

### 1.3 Limitations (why `video=` is not enough for a CRU)

- **Cannot express arbitrary modelines.** No hsync/vsync front-porch/sync-width/polarity fields exist in the parser — timings are always *generated* by CVT/GTF. A monitor that needs vendor-exact timings (most >165 Hz panels do) will show "no signal" or fail validation.
- The generated mode is added by `drm_helper_probe_add_cmdline_mode()` with `DRM_MODE_TYPE_USERDEF`, **appears in the connector's mode list to all userspace** (any Wayland compositor sees it), and fbcon prefers it — but it still passes through `__drm_helper_update_and_validate()` and can be **pruned** by the driver's `mode_valid` (pixel-clock/link limits, § 6).
- One mode per connector; requires reboot (it's parsed once at driver init).
- On fixed-mode panels (eDP), drivers may ignore or refuse timings that differ from the panel's native timing.

### 1.4 Finding DRM connector names

Names are `<type>-<per-type index>`: `DP-1`, `HDMI-A-1`, `eDP-1`, `DVI-D-1`, `Virtual-1`, `Writeback-1`. In sysfs they carry a `cardN-` prefix which must be **stripped** for `video=` and `drm.edid_firmware=`:

```bash
for p in /sys/class/drm/card*-*; do
    echo "$p  status=$(cat $p/status)  enabled=$(cat $p/enabled)"
done
# e.g. /sys/class/drm/card1-DP-1  →  connector name "DP-1"
```

Richer tooling: `drm_info` (JSON output with `-j`, ideal for a utility backend, https://gitlab.freedesktop.org/emersion/drm_info) and `modetest -c` (libdrm). Caveat for multi-GPU boxes: the cmdline matches by connector *name* only; if two cards both have a `DP-1`, the setting applies to whichever matches — and card numbering (`card0`/`card1`) is not stable across boots, though the connector names within a driver are.

---

## 2. EDID firmware override (`drm.edid_firmware`)

### 2.1 Parameter

```
drm.edid_firmware=edid/mymonitor.bin                          # all connectors
drm.edid_firmware=DP-1:edid/mymonitor.bin                     # one connector
drm.edid_firmware=DP-1:edid/a.bin,HDMI-A-1:edid/b.bin         # per-connector list
```

- Requires `CONFIG_DRM_LOAD_EDID_FIRMWARE=y` (enabled by all mainstream distros; it's a bool, not a module).
- Path is relative to the firmware search path — canonically **`/usr/lib/firmware/`** (Arch/Fedora; `/lib/firmware` is a symlink on merged-usr systems, and Debian/Ubuntu docs still say `/lib/firmware`). Convention: `/usr/lib/firmware/edid/<file>.bin`. The `edid/` subdirectory is convention, not requirement — the parameter takes any relative path.
- **History:** introduced 2012 as `drm_kms_helper.edid_firmware`; moved to `drm.edid_firmware` with per-connector support in **kernel 4.15** (Jani Nikula, "drm: handle override and firmware EDID at drm_do_get_edid() level"). The old spelling survived as a deprecated alias for a while; on any 2025+ kernel use `drm.edid_firmware`. Sources: [Monado EDID override guide](https://monado.freedesktop.org/edid-override.html), [0xf8.org analysis of the 4.15 rework](https://www.0xf8.org/2020/12/why-your-kernels-drm-edid_firmware-parameter-doesnt-work-anymore-in-libvirt-environments/).
- **Modern implementation detail (kernel ≥ ~6.1):** firmware EDID is fetched **at connector detect time** through the same override container as debugfs (`drm_edid_override_get()` falls back to `drm_edid_load_firmware()` — verified in current [drm_edid.c](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/drm_edid.c)). Consequences: (a) the file is requested on *every* detect, so it must be resolvable whenever a hotplug happens; (b) the blob is fully validated — bad checksum ⇒ silently ignored with a debug log; (c) because the module parameter is **runtime-writable** (verified locally: `/sys/module/drm/parameters/edid_firmware` is mode `0644`), you can set it after boot and force a reprobe — see § 4.3.

### 2.2 The initramfs requirement

**When it's needed:** whenever the GPU driver initializes before the real root filesystem is mounted — i.e., early KMS (driver in initramfs, which is the default for amdgpu/i915 on Arch/CachyOS, Fedora, openSUSE, Ubuntu with plymouth) or built-in drivers. If the driver probes the connector from the initramfs and the EDID file only exists on the root fs, you get:

```
[drm] *ERROR* [CONNECTOR:xx:DP-1] Requesting EDID firmware "edid/foo.bin" failed (err=-2)
```

(err=-2 = ENOENT; well-documented failure, e.g. [NixOS/nixpkgs #279739](https://github.com/NixOS/nixpkgs/issues/279739).) If the driver loads late (after root mount) it works without initramfs changes — but a robust utility should always install into the initramfs.

**Per-distro:**

```bash
# Arch / CachyOS — mkinitcpio: /etc/mkinitcpio.conf
FILES=(/usr/lib/firmware/edid/mymonitor.bin)
sudo mkinitcpio -P

# dracut (Fedora, openSUSE): /etc/dracut.conf.d/99-edid.conf
install_items+=" /usr/lib/firmware/edid/mymonitor.bin "
sudo dracut -f --regenerate-all

# Debian/Ubuntu — initramfs-tools: hook file /etc/initramfs-tools/hooks/edid
#!/bin/sh
[ "$1" = prereqs ] && { echo; exit 0; }
. /usr/share/initramfs-tools/hook-functions
mkdir -p "${DESTDIR}/lib/firmware/edid"
cp /lib/firmware/edid/mymonitor.bin "${DESTDIR}/lib/firmware/edid/"
# then: chmod +x the hook && sudo update-initramfs -u -k all
```

(Monado's guide confirms `update-initramfs -u -k all` for Debian-family: https://monado.freedesktop.org/edid-override.html)

### 2.3 Built-in generic EDIDs: **removed in kernel 6.9**

Historically `CONFIG_DRM_LOAD_EDID_FIRMWARE` shipped built-in blobs selectable without any file: `edid/800x600.bin`, `edid/1024x768.bin`, `edid/1280x1024.bin`, `edid/1600x1200.bin`, `edid/1680x1050.bin`, `edid/1920x1080.bin`, generated from `Documentation/admin-guide/edid` sources. **Maxime Ripard removed them (patch "drm/edid/firmware: Remove built-in EDIDs", Feb 2024), merged in the drm pull for 6.9-rc1** — rationale: they ignored connector type and produced EDIDs invalid for the actual connector. The generator sources under `Documentation/admin-guide/edid` went with them; [admin-guide/edid.rst](https://www.kernel.org/doc/html/latest/admin-guide/edid.html) is now a stub. Sources: [dri-devel patch](https://lists.freedesktop.org/archives/dri-devel/2024-February/442378.html), [v2 thread](https://www.mail-archive.com/dri-devel@lists.freedesktop.org/msg482203.html).

**Implication:** on kernel ≥ 6.9 every EDID name must correspond to a real file you provide. Old tutorials referencing `drm.edid_firmware=edid/1920x1080.bin` without a file are broken. Your utility must always ship/generate the blob.

---

## 3. Creating custom EDID binaries

### 3.1 Reading the current EDID

```bash
cat /sys/class/drm/card1-DP-1/edid > current.bin        # raw binary, 128–512+ bytes
edid-decode current.bin                                  # decode + validate
di-edid-decode < current.bin                             # libdisplay-info's decoder
```

The sysfs `edid` attribute is empty when nothing is connected/detected. `edid-decode` lives at https://git.linuxtv.org/edid-decode.git (packaged in most distros, sometimes inside `v4l-utils`); `di-edid-decode` comes from [libdisplay-info](https://gitlab.freedesktop.org/emersion/libdisplay-info) — the same parsing library wlroots/Sway/gamescope use, which makes it the best "will compositors read this correctly" oracle. `edid-decode --check` reports conformance errors and checksum problems.

### 3.2 The modification task (what a CRU actually edits)

An EDID is 128-byte blocks, each ending in a checksum byte such that the block sums to 0 mod 256.

- **Detailed Timing Descriptor (DTD):** 18 bytes; base block holds up to 4 descriptors; the first DTD is the preferred mode. Pixel clock is a **16-bit little-endian field in 10 kHz units ⇒ hard maximum 655.35 MHz**. Practical consequence: 2560x1440 CVT-RB2 tops out around ~165 Hz within a DTD; anything faster (1440p ≥ ~170 Hz, 4K ≥ ~100 Hz) **cannot be expressed as a DTD at all**.
- **CTA-861 extension block (tag 0x02):** carries additional DTDs, the Video Data Block (VIC codes), audio blocks, and crucially the **HDMI Forum VSDB (HF-VSDB)** — required to signal `Max_TMDS_Character_Rate` > 340 MHz and SCDC for HDMI 2.0 rates, and FRL rates for HDMI 2.1. Drivers clamp HDMI modes to 340 MHz TMDS if the EDID lacks it (NVIDIA is notoriously strict here — caps at ~165/340 MHz without proper VSDB/HF-VSDB, per [NVIDIA forum reports](https://forums.developer.nvidia.com/t/4k-resolution-over-hdmi-with-linux-driver/28789)).
- **DisplayID 2.0 extension (tag 0x70), Type VII detailed timings:** the only standard way to describe modes with pixel clock > 655.35 MHz (field is 3 bytes × 10 kHz ⇒ up to ~167 GHz). The kernel parses DisplayID Type I (v1.3) and **Type VII (v2.0) timings since ~5.14** (`drm_edid.c`/`drm_displayid.c`). This is exactly what Windows CRU emits when you add a high-bandwidth "detailed resolution" in a DisplayID extension block. Your Linux CRU needs a DisplayID Type VII writer to be feature-complete.
- **Checksum fixing:** recompute the last byte of every modified block: `sum(block[0:127]) + checksum ≡ 0 (mod 256)`. Kernels ≥ 6.1 **reject** override EDIDs failing `drm_edid_valid()` (all blocks checksummed), so this is mandatory, not cosmetic.

```python
def fix_checksums(edid: bytearray) -> bytearray:
    for i in range(0, len(edid), 128):
        block = edid[i:i+128]
        block[127] = (256 - sum(block[:127])) % 256
        edid[i:i+128] = block
    return edid
```

### 3.3 Tools and libraries (status as of 2025/2026)

| Tool | What it does | Status / fit for CRU project |
|---|---|---|
| [akatrevorjay/edid-generator](https://github.com/akatrevorjay/edid-generator) | `modeline2edid` + Makefile: Xorg modeline (from `cvt`/`gtf`) → 128-byte EDID with valid checksum | The classic; effectively unmaintained; produces **base-block-only** EDIDs (strips extensions ⇒ loses audio/HDR/HDMI2 caps); DTD-limited (≤655 MHz). Packaged as [AUR edid-generator-git](https://aur.archlinux.org/packages/edid-generator-git) |
| [RobertoNegro/edid-generator](https://github.com/RobertoNegro/edid-generator) | Interactive CLI generating full EDIDs (VGA→8K, HDMI/DP, multi-mode, HDR, audio, validation) | Modern (inspired by akatrevorjay's); the closest existing thing to a CRU backend — study it |
| [wxEDID](https://sourceforge.net/projects/wxedid/) | GTK GUI structured EDID editor: edit DTDs/CTA blocks field-by-field, auto-recalculates checksums | Maintained enough; the standard GUI answer on Linux; used in the [Monado guide](https://monado.freedesktop.org/edid-override.html) |
| edid-decode / di-edid-decode | Decode/verify | Actively maintained (linuxtv / libdisplay-info) |
| [libdisplay-info](https://gitlab.freedesktop.org/emersion/libdisplay-info) | C parsing library (CTA-861, DisplayID) | Actively maintained, freedesktop; **parse-only** (no writer yet) |
| [pyedid (jojonas)](https://github.com/jojonas/pyedid), [pyedid (dd4e)](https://github.com/dd4e/pyedid) | Python EDID **parsers** | Low-activity; parse-only; jojonas's can convert Windows registry exports |
| edid-rs / Kaitai edid spec | Rust/declarative parsers | Parse-only |

**Key gap finding:** there is **no maintained library that *writes* EDIDs** (DTD + CTA-861 + DisplayID + checksums). Every existing workflow is either shell-script assembly (edid-generator), manual hex/GUI editing (wxEDID), or "do it in Windows CRU and export". A serialization layer is the core piece your utility must build.

### 3.4 How Windows CRU users port profiles today

Documented workflow ([marek-g's guide](https://marek-g.github.io/posts/tips_and_tricks/wayland_custom_resolution/), [szymonwilczek gist](https://gist.github.com/szymonwilczek/b3893d11d4b4927d2923badd9f141d06)):

1. Dump Linux EDID: `cat /sys/class/drm/card1-DP-1/edid > mon.bin` (or use the EDID CRU already sees on a dual-boot Windows install).
2. Open in **CRU** on Windows — or **under Wine**, which works, with the caveat that Wine reports a generic "Microsoft" monitor so exporting from real Windows preserves identity better.
3. Add detailed resolutions / extension blocks in CRU, use CRU's **Export** button → `.bin`.
4. Copy to `/usr/lib/firmware/edid/`, set `drm.edid_firmware=...`, rebuild initramfs, reboot.

CRU's exported `.bin` is a plain EDID blob — directly consumable by the kernel. jojonas/pyedid can also convert CRU-style Windows registry EDID overrides. This interop (import CRU exports, produce CRU-compatible bins) is cheap to support and high-value.

---

## 4. Runtime (no-reboot) methods

### 4.1 debugfs `edid_override` — verified current in master `drm_debugfs.c`

Per-connector, generic across all KMS drivers, root-only, requires `CONFIG_DEBUG_FS` (may be restricted under kernel lockdown/secure-boot policies on some distros):

```bash
# N = DRM device minor (0, 1, ...), directory name has NO cardN- prefix
sudo sh -c 'cat my.bin > /sys/kernel/debug/dri/1/DP-1/edid_override'
sudo sh -c 'echo reset > /sys/kernel/debug/dri/1/DP-1/edid_override'   # clear
```

Semantics on kernels ≥ ~6.1 (verified in current `drm_edid.c`):
- The write is validated with `drm_edid_valid()` — **bad header/checksum ⇒ `-EINVAL`** (older kernels accepted garbage; a utility must handle both).
- The override is stored on the connector and consulted in `_drm_do_get_edid()` **before DDC** — i.e., it takes effect **only at the next detect/probe**. No automatic reprobe happens; the kernel only logs `[CONNECTOR:...] EDID override set`.
- It has priority over `drm.edid_firmware` (firmware is the fallback when no debugfs override exists — same container).

Sibling debugfs files per connector (current master): `force` (write `on` / `digital` / `off` / `unspecified`), read-only `vrr_range`, `output_bpc`, and for HDMI an `infoframes/` directory.

### 4.2 Triggering the reprobe

Three options, most-generic first:

```bash
# (a) Generic, all drivers — sysfs status is WRITABLE (verified drm_sysfs.c status_store):
#     values: detect | on | on-digital | off  → sets connector->force, calls fill_modes()
echo detect | sudo tee /sys/class/drm/card1-DP-1/status

# (b) amdgpu-specific — full link rediscovery incl. EDID/DPCD re-read + hotplug event
#     (verified amdgpu_dm_debugfs.c: 1 = simulate plug, 0 = simulate unplug)
echo 1 | sudo tee /sys/kernel/debug/dri/1/DP-1/trigger_hotplug

# (c) Physical: unplug/replug the cable
```

Both still work on 6.x+ kernels. `trigger_hotplug` is the most thorough on amdgpu (calls `dc_link_detect(DETECT_REASON_HPD)` and `amdgpu_dm_update_connector_after_detect()`, emits a real hotplug uevent so compositors re-enumerate). The generic `status` write reprobes modes, but some compositors won't refresh their mode list without a hotplug uevent — for a utility, prefer (b) on amdgpu and fall back to (a)+`udevadm trigger` elsewhere. i915 re-reads on (a) reliably.

**Runtime caveat vs boot-time:** overriding at runtime can race with the compositor's cached state; VRR/HDR properties derived from the old EDID may persist until the compositor reinitializes the output. Boot-time firmware override is the reliable end-state; runtime is the "test before you persist" path — exactly the Apply/Persist split a CRU UI wants.

### 4.3 Bonus runtime path: the module parameter is writable

Verified locally: `/sys/module/drm/parameters/edid_firmware` is mode **0644**, and since 4.15 firmware EDID is loaded *at each detect*. Therefore, without any reboot:

```bash
sudo cp my.bin /usr/lib/firmware/edid/my.bin
echo 'DP-1:edid/my.bin' | sudo tee /sys/module/drm/parameters/edid_firmware
echo detect | sudo tee /sys/class/drm/card1-DP-1/status   # or trigger_hotplug
```

This survives compositor restarts (unlike debugfs on some drivers) but not reboots — pair it with the cmdline for persistence.

### 4.4 Forcing connector status

`echo on > /sys/class/drm/cardX-DP-1/status` forces a disconnected connector to appear connected (headless/dummy-plug scenarios, combine with an EDID override so there are modes to expose); `off` hard-disables one. Persistent equivalents: `video=DP-1:e` / `video=DP-1:d`.

---

## 5. Driver caveats

- **amdgpu:** honors `drm.edid_firmware` and debugfs override (both flow through core `drm_edid.c`). Known issues: (1) the classic ENOENT-from-initramfs failure ([nixpkgs #279739](https://github.com/NixOS/nixpkgs/issues/279739)); (2) DC-specific quirks where an override EDID's capabilities disagree with the physical connector — being fixed as recently as **March 2026** ([PATCH "drm/amd: fix HDMI signal type for EDID overrides"](https://lkml.iu.edu/hypermail/linux/kernel/2603.1/14378.html) — DC now trusts the physical connector type over the override); (3) FreeSync range comes from the EDID, so an override missing the range descriptor kills VRR (check `debugfs .../vrr_range` after applying). Has the best runtime story via `trigger_hotplug`.
- **i915:** the reference implementation (Jani Nikula, who maintains the override code, is Intel). `drm.edid_firmware`, debugfs override, `video=` all work as documented. No special caveats.
- **nouveau:** uses core helpers, works on modern kernels; there was a real bug where it ignored `drm.edid_firmware` (Red Hat [bug 1677021](https://bugzilla.redhat.com/show_bug.cgi?id=1677021), ~4.20-era, since fixed). Fine on 6.x.
- **NVIDIA proprietary (`nvidia-drm`) — brief, covered in depth in the NVIDIA report:** it does **not** use the kernel's EDID fetch path, so **`drm.edid_firmware` alone is silently ignored** ([Arch forums](https://bbs.archlinux.org/viewtopic.php?id=280081), [NVIDIA forums](https://forums.developer.nvidia.com/t/nvidia-driver-ignoring-custom-edid-using-drm-edid-firmware/229658)). Community findings: combining with `video=<conn>:e` makes the firmware EDID take on some driver versions; the debugfs `edid_override` write works as a runtime workaround; the driver reads it at boot/modeset and won't hot-reload; overrides can disable VRR ("VRR capable: immutable range 0-1-0", [NVIDIA forum](https://forums.developer.nvidia.com/t/overriding-edid-makes-vrr-stop-working-under-wayland-vrr-capable-immutable-range-0-1-0/302929/3)); regressions occur across driver releases (e.g. [580.105.08](https://forums.developer.nvidia.com/t/custom-edid-stopped-working-after-updating-to-580-105-08/351963)). Under X it has its own `CustomEDID` option. The utility must special-case NVIDIA.
- **Virtual drivers (qxl/virtio/vmwgfx):** since the 4.15 rework, override application depends on the driver actually going through `drm_do_get_edid()` at detect; some virtual drivers historically bypassed it ([0xf8.org write-up](https://www.0xf8.org/2020/12/why-your-kernels-drm-edid_firmware-parameter-doesnt-work-anymore-in-libvirt-environments/)).

---

## 6. What an EDID override CANNOT bypass

Verified in `drm_probe_helper.c`: *every* mode — probed, cmdline (`USERDEF`), or from an override EDID — passes `__drm_helper_update_and_validate()` (driver `mode_valid` callbacks, encoder/CRTC pipeline checks) and losers are removed by `drm_mode_prune_invalid()`. Concretely:

1. **DP link bandwidth:** computed from **DPCD** (sink lane count × link rate read over AUX), not from EDID. A mode needing more than `lanes × rate × 0.8` (8b/10b) is pruned. Example: 2560x1440@180 CVT-RB2 ≈ 746 MHz pclk × 24 bpp ≈ 17.9 Gb/s ⇒ **exceeds HBR2×4 (17.28 Gb/s); requires HBR3×4 or DSC.** No EDID edit changes DPCD. (Same for MST branch bandwidth.)
2. **HDMI source-side TMDS/FRL limits:** the GPU's own encoder max (340 MHz for HDMI 1.4-class, 600 MHz for 2.0, FRL rates for 2.1) is a hardware/VBIOS property. EDID can *raise the sink-side claim* (HF-VSDB `Max_TMDS_Character_Rate`) — which is legitimately what pixel-clock-patching does — but never the source cap.
3. **DP++ dual-mode adapters:** the kernel probes the adapter itself (`drm_dp_dual_mode_detect()`) and clamps to the adapter's advertised TMDS limit (165 MHz for type-1 passive adapters) regardless of monitor EDID; bypassing requires kernel patching ([hansmi/fake-dp-dual-mode](https://github.com/hansmi/fake-dp-dual-mode)).
4. **PLL/clock table limits per ASIC**, eDP panel constraints, and `max_bpc`-vs-bandwidth trade-offs (drivers may auto-drop to 8 bpc or 4:2:0 rather than prune, depending on driver).

This mirrors Windows CRU exactly: CRU also cannot exceed link capability — so the UI should surface *why* a mode vanished (compare requested pclk against link budget; amdgpu logs pruning at `drm.debug=0x4`).

**Diagnostic:** boot with `drm.debug=0x104 log_buf_len=4M` to see EDID parse + mode pruning decisions in `dmesg`.

---

## 7. End-to-end recipe: add 2560x1440@180 to DP-1 on amdgpu (any Wayland compositor)

Preliminary math: 1440p180 CVT-RB2 ⇒ ~2640×1570 total @ 180 ≈ **746 MHz pixel clock**. That exceeds the 655.35 MHz DTD ceiling ⇒ the timing must go in a **DisplayID Type VII** block (kernel ≥ 5.14 parses it) — or you accept ~165 Hz max via plain DTD. It also needs DP HBR3 (or DSC); check `dmesg | grep -i "link rate"` or drm_info first.

```bash
# ---- 0. Identify connector & dump current EDID --------------------------------
for p in /sys/class/drm/card*-*; do echo "$p: $(cat $p/status)"; done
# → card1-DP-1: connected           connector name: DP-1, DRM minor: 1
cat /sys/class/drm/card1-DP-1/edid > ~/edid-orig.bin
edid-decode ~/edid-orig.bin        # note existing blocks, max TMDS, DisplayID presence

# ---- 1. Compute the timing ------------------------------------------------------
cvt -r 2560 1440 180               # CVT-RB (v1); for RB2/RB3 use edid-decode's
                                   # timing calculator or CRU's "CVT-RB2 standard"

# ---- 2. Build the modified EDID -------------------------------------------------
# Option A (GUI): wxedid ~/edid-orig.bin
#   - if pclk ≤ 655.35 MHz: edit/add a DTD in the base block or CTA block
#   - for 180 Hz (746 MHz): add/extend a DisplayID 2.0 extension, Type VII timing
#   - wxEDID recalculates block checksums on save → ~/edid-180.bin
# Option B (proven interop path): open edid-orig.bin in Windows CRU (native or Wine),
#   add 2560x1440@180 as a Detailed Resolution with CVT-RB2 timing (CRU places it
#   in a DisplayID block automatically when >655 MHz), Export → edid-180.bin
# Option C (scripted, ≤655 MHz modes only): akatrevorjay/edid-generator:
#   ./modeline2edid - <<< 'Modeline "2560x1440_165" 645.25 2560 2608 2640 2720 1440 1443 1448 1481 +hsync -vsync'
#   make

# ---- 3. Verify — do not skip ----------------------------------------------------
edid-decode --check ~/edid-180.bin       # must report zero checksum/conformance fails
di-edid-decode < ~/edid-180.bin          # what libdisplay-info compositors will see

# ---- 4. Test at RUNTIME first (reversible, no reboot) ---------------------------
sudo sh -c 'cat ~/edid-180.bin > /sys/kernel/debug/dri/1/DP-1/edid_override'
echo 1 | sudo tee /sys/kernel/debug/dri/1/DP-1/trigger_hotplug     # amdgpu re-detect
# compositor sees a hotplug; check the mode arrived:
cat /sys/class/drm/card1-DP-1/modes | grep 2560x1440
# select 180 Hz in the compositor (or: wlr-randr / kscreen-doctor / gnome settings)
# if it vanished: dmesg | grep -iE 'prun|edid|link' — likely link-bandwidth pruning
# rollback: echo reset > .../edid_override && echo 1 > .../trigger_hotplug

# ---- 5. Persist across reboots --------------------------------------------------
sudo install -Dm644 ~/edid-180.bin /usr/lib/firmware/edid/edid-180.bin

# kernel cmdline (GRUB: /etc/default/grub GRUB_CMDLINE_LINUX_DEFAULT; or the
# systemd-boot entry / kernelstub / ZFSBootMenu property):
#     drm.edid_firmware=DP-1:edid/edid-180.bin
sudo grub-mkconfig -o /boot/grub/grub.cfg          # if GRUB

# initramfs (Arch/CachyOS shown; § 2.2 for dracut/initramfs-tools):
sudo sed -i 's|^FILES=(|FILES=(/usr/lib/firmware/edid/edid-180.bin |' /etc/mkinitcpio.conf
sudo mkinitcpio -P

sudo reboot

# ---- 6. Post-reboot verification -------------------------------------------------
dmesg | grep -i edid                                # no "failed (err=-2)"
cat /sys/class/drm/card1-DP-1/modes | head
sudo cat /sys/kernel/debug/dri/1/DP-1/vrr_range     # confirm FreeSync survived
```

Works identically under GNOME/Mutter, KDE/KWin, wlroots compositors, and Xorg — they all consume the connector mode list the kernel built from the (overridden) EDID.

---

## Key takeaways for the CRU utility architecture

1. **EDID override is the universal backend**; `video=` is only a helper for standard CVT/GTF modes and connector force-enable/disable.
2. Ship your own EDID **writer** (DTD + CTA-861 HF-VSDB + DisplayID Type VII + checksums) — no maintained library exists; validate output with `edid-decode --check`/libdisplay-info; import/export CRU-compatible `.bin`.
3. Implement the **runtime-test → persist** flow: debugfs `edid_override` + `trigger_hotplug`(amdgpu)/`status=detect` for instant preview, then firmware file + cmdline + per-distro initramfs integration (mkinitcpio/dracut/initramfs-tools) for persistence.
4. Handle kernel-version matrix: ≥6.9 no built-in EDIDs; ≥~6.1 strict override validation (`-EINVAL` on bad checksum); ≥5.14 DisplayID Type VII; ≥4.15 `drm.edid_firmware` spelling.
5. Surface link-budget math (DTD 655.35 MHz ceiling, DP lane×rate, HDMI TMDS/FRL) to explain pruned modes; special-case NVIDIA proprietary.

### Sources

- https://www.kernel.org/doc/html/latest/fb/modedb.html — `video=` syntax
- https://www.kernel.org/doc/html/latest/admin-guide/edid.html — EDID firmware admin guide (post-6.9 stub)
- https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/drm_modes.c — cmdline parsing, CVT/GTF selection
- https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/drm_edid.c — override validation & firmware fallback
- https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/drm_debugfs.c — `edid_override`, `force`, `vrr_range`
- https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/drm_sysfs.c — writable connector `status`
- https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/drm_probe_helper.c — USERDEF mode injection & pruning
- https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/amd/display/amdgpu_dm/amdgpu_dm_debugfs.c — `trigger_hotplug`
- https://lists.freedesktop.org/archives/dri-devel/2024-February/442378.html and https://www.mail-archive.com/dri-devel@lists.freedesktop.org/msg482203.html — built-in EDID removal (6.9)
- https://monado.freedesktop.org/edid-override.html — override guide incl. initramfs
- https://marek-g.github.io/posts/tips_and_tricks/wayland_custom_resolution/ — CRU-to-Linux porting workflow
- https://www.0xf8.org/2020/12/why-your-kernels-drm-edid_firmware-parameter-doesnt-work-anymore-in-libvirt-environments/ — 4.15 rework analysis
- https://github.com/NixOS/nixpkgs/issues/279739 — amdgpu ENOENT/initramfs failure
- https://bbs.archlinux.org/viewtopic.php?id=280081, https://forums.developer.nvidia.com/t/nvidia-driver-ignoring-custom-edid-using-drm-edid-firmware/229658, https://forums.developer.nvidia.com/t/custom-edid-stopped-working-after-updating-to-580-105-08/351963, https://forums.developer.nvidia.com/t/overriding-edid-makes-vrr-stop-working-under-wayland-vrr-capable-immutable-range-0-1-0/302929/3 — NVIDIA behavior
- https://bugzilla.redhat.com/show_bug.cgi?id=1677021 — historical nouveau bug
- https://lkml.iu.edu/hypermail/linux/kernel/2603.1/14378.html — 2026 amdgpu EDID-override fix
- https://github.com/akatrevorjay/edid-generator, https://github.com/RobertoNegro/edid-generator, https://sourceforge.net/projects/wxedid/, https://git.linuxtv.org/edid-decode.git, https://gitlab.freedesktop.org/emersion/libdisplay-info, https://github.com/jojonas/pyedid, https://github.com/dd4e/pyedid — tooling
- https://github.com/hansmi/fake-dp-dual-mode — DP++ adapter clamp
- https://gist.github.com/szymonwilczek/b3893d11d4b4927d2923badd9f141d06 — Wayland custom resolution gist
