# Linux CRU — Display Mode Research Corpus (Synthesis)

*Compiled 2026-08-10 from four deep-dive reports in [`docs/research/`](research/):*
- [`wayland-compositors.md`](research/wayland-compositors.md) — per-compositor commands & capabilities
- [`kernel-drm-edid.md`](research/kernel-drm-edid.md) — the DRM/KMS layer: `video=`, EDID overrides, debugfs
- [`nvidia.md`](research/nvidia.md) — NVIDIA proprietary/open/nouveau, X11 + Wayland
- [`amd-intel.md`](research/amd-intel.md) — amdgpu/i915/xe, X11 + Wayland, safety/recovery

---

## 1. The mental model (read this first)

Everything on Linux flows from one fact: **the kernel builds each connector's mode list by parsing the monitor's EDID**, and everyone upstream (Xorg, every Wayland compositor) consumes that list. Getting a custom mode in means either:

1. **Injecting a mode above the kernel** (xrandr on X11, compositor config on Wayland) — fast, session-scoped, but only works where the display server/driver allows it; **or**
2. **Lying to the kernel about the EDID** (EDID override) — universal, works under *every* compositor and Xorg, because the custom mode simply appears as a normal EDID mode. This is literally what Windows CRU does via the registry.

Two validation layers exist no matter which path you take, and **neither can be fully bypassed**:
- **EDID-claimed limits** (max pixel clock, HDMI Max_TMDS) — bypassable by *editing the EDID* (that's the point of a CRU).
- **Hardware/link limits** (DP link training/DPCD bandwidth, GPU encoder TMDS/FRL caps, DP++ adapter clamps, DSC feasibility) — not bypassable by anyone, on any OS. Modes exceeding them are silently pruned; a good tool computes the link budget and explains *why* a mode vanished.

## 2. Support matrix — what works where

| | **X11** | **Wayland** |
|---|---|---|
| **AMD (amdgpu) / Intel (i915/xe)** | `cvt`/`gtf` → `xrandr --newmode/--addmode/--output --mode` works out of the box (kernel validates at commit: "Configure crtc failed" = bandwidth). Persist via xorg.conf.d Modeline or `~/.xprofile`. No "loosen validation" module param exists — EDID override is how you raise EDID-claimed limits. | **EDID override = universal** (drm core, works perfectly). Compositor-native: sway/Hyprland full modelines; wlr-randr `--custom-mode` (CVT) on all wlroots; KWin 6.6+ `kscreen-doctor addCustomMode` (libxcvt CVT/CVT-RB); **GNOME: nothing, EDID override only**; COSMIC: custom modes currently broken. |
| **NVIDIA proprietary (incl. open kernel modules)** | xorg.conf `Modeline` + `Option "ModeValidation" "AllowNonEdidModes, NoEdidMaxPClkCheck, ..."` (the current app's path — mostly right). `xrandr --addmode` gives BadMatch *until* ModeValidation is relaxed, then works for live testing. `Option "CustomEDID" "DP-0:/path.bin"` = exact Windows-CRU equivalent, keeps VRR. `IgnoreEDID` **no longer exists** (our current code emits it!). | **EDID override is the ONLY lever** — honored since driver ≥ 535 + kernel ≥ 6.2 + `nvidia-drm.modeset=1`. Compositor modelines and `video=` are rejected by NVKMS validation; no ModeValidation equivalent exists on Wayland. Known bugs: override kills VRR (`vrr_capable=0`, NVIDIA bug 4797139), DSC displays may ignore overrides. |
| **nouveau** | Standard: xrandr works | Standard: EDID override + `video=` work; `nouveau.hdmimhz=` raises HDMI clamp |

**Connector naming trap:** NVIDIA X names (`DP-0`, `HDMI-0`) ≠ kernel DRM names (`DP-1`, `HDMI-A-1`); xf86-video-amdgpu uses `DisplayPort-0`-style names while modesetting uses kernel names. The tool needs an explicit name map per backend.

## 3. The universal backend: EDID override (kernel DRM)

The one mechanism that works on **every GPU × every compositor**:

- **Test (no reboot):** write blob to `/sys/kernel/debug/dri/<minor>/<CONN>/edid_override`, then reprobe (`echo 1 > .../trigger_hotplug` on amdgpu; `echo detect > /sys/class/drm/cardX-CONN/status` generic). Kernel ≥ 6.1 validates checksums (`-EINVAL` on bad blob). Compositors see a hotplug and refresh their mode lists live. Root required; blocked under Secure Boot lockdown. Also: `/sys/module/drm/parameters/edid_firmware` is runtime-writable — a middle path that survives compositor restarts.
- **Persist:** file in `/usr/lib/firmware/edid/`, `drm.edid_firmware=CONN:edid/file.bin` on the kernel cmdline, **and embed the file in the initramfs** (mkinitcpio `FILES=`, dracut `install_items+=`, initramfs-tools hook) or it silently fails with early KMS. Keep a fallback boot entry — that's the recovery story.
- **Kernel version gates:** ≥ 4.15 `drm.edid_firmware` spelling; ≥ ~6.1 strict validation; ≥ 5.14 DisplayID Type VII parsing; **≥ 6.9 built-in generic EDIDs removed** (we must always ship the blob).
- **EDID format essentials:** DTD pixel clock caps at **655.35 MHz** — anything faster (1440p ≥ ~170 Hz, 4K ≥ ~100 Hz) needs a **DisplayID 2.0 Type VII** timing block. HDMI > 340 MHz needs an HF-VSDB with raised Max_TMDS. Start from the *dumped* EDID and patch minimally — never synthesize from scratch (loses DSC/VRR/audio blocks). Recompute per-block checksums.
- **Key gap in the ecosystem:** no maintained library *writes* EDIDs. Parsers exist (libdisplay-info, pyedid, edid-decode); writers are dead shell scripts (edid-generator) or GUI hex editors (wxEDID). **An EDID patcher is the core new component this project must build** — and it should import/export Windows-CRU-compatible `.bin` files (they're plain EDID blobs).

## 4. Fast paths (session-scoped, no root, instant) — offer when available

| Environment | Command | Notes |
|---|---|---|
| X11 AMD/Intel | `xrandr --newmode <modeline>` + `--addmode` + `--output --mode` | The classic; great for live testing before persisting |
| X11 NVIDIA | same, after xorg.conf ModeValidation write + X restart | or CustomEDID (no flags needed, keeps VRR) |
| sway | `swaymsg 'output DP-1 modeline <full modeline>'` | full timing control |
| Hyprland | `hyprctl keyword monitor "DP-1, modeline <...>, 0x0, 1"` | hyprlang ≤ 0.54; Lua config from 0.55 |
| KDE Plasma ≥ 6.6 | `kscreen-doctor output.X.addCustomMode.W.H.<mHz>[.reduced]` then `output.X.mode.<...>` | two non-atomic steps; CVT computed by KWin |
| wlroots misc (labwc, river, niri, Wayfire) | `wlr-randr --output X --custom-mode WxH@RHz` | CVT full-blanking, computed by compositor |
| GNOME (any), COSMIC, KDE < 6.6 | — none — | EDID override only |

On NVIDIA+Wayland all compositor fast paths fail driver validation — skip straight to EDID override.

## 5. Timing generation

- Keep our own modeline math but fix it: support **CVT, CVT-RB (v1: refresh must be multiple of 60), and CVT-RB2 (arbitrary refresh, lowest pixel clock — the overclocker's choice)**. The current `calculate_cvt_rb2_modeline()` is wrong (nonsense h_period formula, `width*0.3` blanking); replace with the real VESA CVT 1.2 algorithm (reference: kevinlekiller's `cvt12.c`, or link `libxcvt` like KWin does).
- Reduced blanking is the single biggest lever for refresh overclocking (1080p75 RB2 ≈ 156 MHz vs 207 MHz full-blanking).
- Fractional refresh rates can't be expressed via `video=` or KWin's mHz-integer API but can in modelines and EDID DTDs.

## 6. Safety: test-then-persist (non-negotiable design)

1. **Always test before persisting.** X11: apply via xrandr, countdown dialog (KDE uses 15 s, GNOME 20 s), auto-revert on timeout; belt-and-braces `systemd-run --on-active=20s` revert that survives a tool crash. Wayland: runtime debugfs EDID + hotplug, revert = `echo reset` + re-trigger.
2. Wayland compositors use atomic TEST_ONLY commits — driver-invalid modes fail cleanly; the real risk is the *monitor* rejecting a valid-to-the-GPU signal, hence the countdown.
3. Persisting the EDID override: never touch the only boot entry; document the GRUB-edit recovery (`e`, delete `drm.edid_firmware=...`); `nomodeset` as last resort.
4. Verify after apply: mode present in `/sys/class/drm/.../modes`, `vrr_capable` still 1, `dmesg` free of pruning/EDID errors. Failure modes to detect: frame skipping (panel drops every Nth frame), silent 4:2:0 downgrade on HDMI, lost audio/VRR from mangled CTA blocks.

## 7. What this means for linux-cru's architecture

Current state: a Tk GUI that writes an NVIDIA-flavored xorg.conf (with the long-removed `IgnoreEDID` option and a bogus `CustomEDID` modprobe line) — i.e. it half-covers one cell of the matrix. Target architecture:

```
┌─ GUI (existing Tk, evolved) ──────────────────────────────┐
│  display picker · mode editor · link-budget calculator    │
│  test-with-countdown · persist · revert/uninstall         │
└──────┬────────────────────────────────────────────────────┘
┌──────▼──── Detection layer ───────────────────────────────┐
│ session (X11/Wayland) · compositor+version · GPU driver   │
│ (amdgpu/i915/xe/nouveau/nvidia+version) · kernel version  │
│ · lockdown/secure-boot · initramfs system · bootloader    │
│ · connector name maps (DRM ↔ X ↔ DDX)                     │
└──────┬────────────────────────────────────────────────────┘
┌──────▼──── Core engines ──────────────────────────────────┐
│ Timing engine: CVT / CVT-RB / CVT-RB2 (real VESA math)    │
│ EDID engine: dump · parse · patch DTD/CTA/DisplayID-VII   │
│   · checksums · validate (edid-decode) · CRU .bin interop │
│ Link-budget calculator: DP lanes×rate, HDMI TMDS/FRL, DSC │
└──────┬────────────────────────────────────────────────────┘
┌──────▼──── Apply backends (pick by detection) ────────────┐
│ 1. EDID override (universal): debugfs test → firmware +   │
│    cmdline + initramfs persist                            │
│ 2. X11 xrandr (AMD/Intel live) · xorg.conf writer         │
│    (Modeline+ModeValidation / CustomEDID for NVIDIA)      │
│ 3. Compositor-native: swaymsg/hyprctl modelines ·         │
│    kscreen-doctor addCustomMode (Plasma ≥ 6.6) ·          │
│    wlr-randr --custom-mode                                │
└───────────────────────────────────────────────────────────┘
```

Sensible build order:
1. **Detection layer + honest UI** — tell the user which cell of the matrix they're in and which paths are available (removes today's silent NVIDIA/X11-only assumption).
2. **Fix the timing engine** (real CVT/RB/RB2).
3. **X11 runtime path** (xrandr test-with-revert; NVIDIA xorg.conf writer fixed: drop `IgnoreEDID`, drop the bogus modprobe line, add `ModeDebug`).
4. **EDID engine + debugfs test path** — the big one; unlocks all of Wayland including GNOME and NVIDIA.
5. **Persistence plumbing** (firmware install, cmdline edit for GRUB/systemd-boot, initramfs hooks — we already have the mkinitcpio/dracut/update-initramfs detection).
6. **Compositor-native fast paths** as convenience adapters.
