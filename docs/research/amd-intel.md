# Custom Resolutions & Refresh Rates on AMD (amdgpu) and Intel (i915/xe) GPUs — Linux, X11 + Wayland

Technical research report for extending a Linux CRU-style GUI beyond X11+NVIDIA. Verified against Arch Wiki, kernel docs/mailing lists, KDE/GNOME/Hyprland/sway upstream, and community threads (state of the world: mid-2026).

**Executive summary for the tool design:**

- On **X11**, both AMD and Intel go through the standard RandR path: `cvt`/`gtf` → `xrandr --newmode/--addmode/--output --mode`. The DDX (xf86-video-amdgpu, xf86-video-intel, or modesetting) does not do meaningful extra validation — the *kernel* driver (amdgpu DC / i915 / xe) validates the mode at commit time, so a mode can be added successfully and still fail with `xrandr: Configure crtc N failed` when applied.
- On **Wayland**, there is no universal xrandr equivalent. The portable mechanism is a **kernel-level EDID override** (`drm.edid_firmware=` boot param, or the debugfs `edid_override` at runtime), which works identically for amdgpu, i915 and xe because it lives in DRM core. Compositor-native custom modes exist only on wlroots (sway `modeline`, Hyprland `modeline`) and are landing in KWin (Plasma ~6.6); GNOME/mutter has none.
- There is **no amdgpu/i915 module parameter that loosens mode validation** (nothing like NVIDIA's `ModeValidation=AllowNonEDIDModes`). Validation is bypassed by feeding the driver a *plausible* EDID (higher max pixel clock / Max TMDS clock / DTD), i.e., exactly what Windows CRU does — which is good news: the CRU model maps 1:1 onto the DRM stack.

---

## 1. X11 + amdgpu / modesetting

### 1.1 The classic runtime workflow

Generate a modeline (CVT standard, CVT reduced-blanking, or GTF):

```bash
$ cvt 2560 1440 75
# 2560x1440 74.97 Hz (CVT) hsync: 111.86 kHz; pclk: 397.75 MHz
Modeline "2560x1440_75.00"  397.75  2560 2760 3040 3520  1440 1443 1448 1492 -hsync +vsync

$ cvt -r 1920 1080 60          # reduced blanking — much lower pixel clock
Modeline "1920x1080R"  138.50  1920 1968 2000 2160  1080 1083 1088 1111 +hsync -vsync

$ gtf 1920 1080 75             # older GTF formula (what many monitors' OSDs expect)
```

Caveats worth encoding in the tool:
- `cvt -r` (CVT-RB v1) **only accepts refresh rates that are multiples of 60 Hz**. For arbitrary rates with reduced blanking (essential for overclocking, see §4), use kevinlekiller's `cvt12.c`, which implements **CVT 1.2 / RB2** (`./cvt12 1920 1080 72 -b`): https://github.com/kevinlekiller/cvt_modeline_calculator_12 (referenced from https://github.com/kevinlekiller/linux_intel_display_overclocking).
- RB2 timings dramatically cut pixel clock: the Intel overclocking guide's example — 1080p71 is 207.25 MHz with CVT standard blanking but **164.96 MHz with reduced blanking**, squeezing under a 165 MHz single-link limit.

Apply it (drop the word `Modeline`, keep everything else verbatim):

```bash
xrandr --newmode "2560x1440_75.00" 397.75 2560 2760 3040 3520 1440 1443 1448 1492 -hsync +vsync
xrandr --addmode DP-1 "2560x1440_75.00"
xrandr --output DP-1 --mode "2560x1440_75.00"
```

Cleanup / revert primitives the tool needs:

```bash
xrandr --output DP-1 --mode 2560x1440 --rate 60   # back to a known-good EDID mode
xrandr --delmode DP-1 "2560x1440_75.00"           # detach from output
xrandr --rmmode "2560x1440_75.00"                 # delete from server
xrandr --output DP-1 --auto                       # fall back to preferred mode
```

Sources: [ArchWiki: xrandr](https://wiki.archlinux.org/title/Xrandr), [Arch forums: custom modeline threads](https://bbs.archlinux.org/viewtopic.php?id=221315).

**Persistence option A — xorg.conf.d Monitor section** (survives session, applies before login manager):

```
# /etc/X11/xorg.conf.d/10-monitor.conf
Section "Monitor"
    Identifier "DP-1"
    Modeline "2560x1440_75.00"  397.75  2560 2760 3040 3520  1440 1443 1448 1492 -hsync +vsync
    Option "PreferredMode" "2560x1440_75.00"
EndSection
```

Two sharp edges:
1. With the **modesetting** driver the Monitor `Identifier` must match the RandR output name (`DP-1`); with **xf86-video-amdgpu** the connector may be named differently (`DisplayPort-0`, `HDMI-A-0`) — always read names from `xrandr` output, never guess. To bind a Monitor section explicitly, add `Option "Monitor-DP-1" "<Identifier>"` to the `Device` section.
2. Some setups also need a `Screen`/`Display` subsection with `Modes "2560x1440_75.00"` for the mode to become default ([Arch forums example](https://bbs.archlinux.org/viewtopic.php?id=260982)).

**Persistence option B — autostart script** (`~/.xprofile`, sourced by most display managers, or `~/.xinitrc` for startx): just the three xrandr lines. This is what the ArchWiki recommends since `--newmode` state is per-X-session ([ArchWiki: xrandr](https://wiki.archlinux.org/title/Xrandr)). It's the safer default for a GUI tool: a broken mode in `~/.xprofile` can't brick the display manager's greeter the way a bad xorg.conf `PreferredMode` can.

### 1.2 When amdgpu rejects a custom xrandr mode

Key architectural point: `--newmode`/`--addmode` almost always "succeed" — RandR just registers the mode. **Validation happens in the kernel at modeset time.** Failure surfaces as:

- `xrandr: Configure crtc 0 failed` — the KMS atomic/legacy commit was rejected (most common for bandwidth/pixel-clock violations). Check `dmesg` immediately after; DC logs the reason.
- `X Error ... BadMatch` — mode/output mismatch at the RandR level.

What DC (Display Core, the DCN-era display driver inside amdgpu) validates:

- **Pixel clock vs. link bandwidth**: for DP, the mode's bandwidth must fit the trained link (lane count × rate, minus overhead), including **DSC feasibility**; for HDMI, pixel clock must be ≤ the max TMDS clock. The max TMDS clock is taken from the monitor's **HDMI VSDB / HF-VSDB in the EDID** (typically 340 MHz for HDMI 2.0, 600 MHz w/ scrambling), which is exactly the field Windows CRU users edit — freedesktop bug on this behavior: https://bugs.freedesktop.org/show_bug.cgi?id=87508.
- If max TMDS is exceeded, modern DC will silently **downgrade pixel encoding to YCbCr 4:2:2/4:2:0** rather than reject, when possible ([patch: "Reduce HDMI pixel encoding if max clock is exceeded"](https://lists.freedesktop.org/archives/dri-devel/2019-November/247022.html)).
- Older pre-DC hardware paths gained stricter checks recently: ["drm/amdgpu: Respect max pixel clock for HDMI and DVI-D"](https://lists.freedesktop.org/archives/amd-gfx/2025-August/129353.html) (Aug 2025, legacy non-DC code) and ["Reject modes with too high pixel clock on DCE6-10"](https://lkml.iu.edu/2603.3/16243.html) (2026, for 6.12+ stable) — i.e., **validation is getting stricter over time, not looser**. A January 2026 patch also bumped the DP→HDMI adapter safe TMDS limit from 165 to 340 MHz ([amd-gfx](https://lists.freedesktop.org/archives/amd-gfx/2026-January/136218.html)), so passive-adapter users on ≥6.15-era kernels get more headroom.
- **MST/DSC**: DSC pass-through bandwidth is checked hop-by-hop on MST ([patch: "consider DSC pass-through during mode validation"](https://lkml.iu.edu/2208.0/05157.html)); "not enough bandwidth"-type failures on docks/MST hubs come from this path.

**Module params:** there is **no parameter to relax mode validation**. The relevant knobs are debug-oriented ([kernel docs: amdgpu module parameters](https://docs.kernel.org/gpu/amdgpu/module-parameters.html)):
- `amdgpu.dcdebugmask` — bitmask from `DC_DEBUG_MASK` (in `drivers/gpu/drm/amd/include/amd_shared.h`); notably `0x4 = DC_DISABLE_DSC`, `0x10 = DC_DISABLE_PSR`. Disabling DSC (`amdgpu.dcdebugmask=0x4`) is a *diagnostic* for DSC-related mode problems, but it **reduces** available bandwidth — high-refresh 4K modes that need DSC will disappear.
- `amdgpu.dcfeaturemask` — feature enables (FreeSync etc.); community decoder script: https://bbs.archlinux.org/viewtopic.php?id=302858.
- `amdgpu.dc=0` forces the legacy display path on older ASICs — occasionally used to escape a DC validation bug, not viable on DCN (Navi+) hardware.

The correct way to get a mode past validation is the **EDID override** (§2.1): raise the claimed max pixel clock / Max TMDS in the EDID and the same checks pass, since they validate *against the EDID's claims*.

One AMD-specific runtime quirk: after applying a non-native mode, scaling may be wrong; fix with the connector property:

```bash
xrandr --output HDMI-A-0 --set "scaling mode" "Full"
```

([team-simple overclocking guide](https://team-simple.org/forum/viewtopic.php?id=6233), [Arch forums 6700XT 60→75 Hz thread](https://bbs.archlinux.org/viewtopic.php?id=288460)).

### 1.3 modesetting vs. xf86-video-amdgpu (and Intel)

For **custom mode acceptance they are equivalent** — both are thin RandR frontends over the same KMS ioctls; the kernel driver is the gatekeeper. Differences that matter to the tool:

| | xf86-video-amdgpu | modesetting (Xorg built-in) |
|---|---|---|
| Output names | `DisplayPort-0`, `HDMI-A-0` | `DP-1`, `HDMI-A-1` (kernel connector names) |
| `TearFree` | Yes (auto-on for VRR/rotation) | Only in git-master Xorg (merged 2023, not in any 21.1.x release) |
| `VariableRefresh` option | Yes | Yes, since **xorg-server 21.1** |
| Notes | AMD-specific, per-CRTC shadow buffers | Default for Intel (xf86-video-intel is abandoned); increasingly default for AMD on some distros |

Sources: [amdgpu(4) man page](https://manpages.debian.org/testing/xserver-xorg-video-amdgpu/amdgpu.4.en.html), [ArchWiki: AMDGPU](https://wiki.archlinux.org/title/AMDGPU). One community data point: an Arch user's `Configure crtc` failures disappeared after *removing* xf86-video-amdgpu (falling back to modesetting) ([Arch forums](https://bbs.archlinux.org/viewtopic.php?id=299225)) — the tool should report which DDX is active (`grep -E 'AMDGPU|modeset' /var/log/Xorg.0.log`) when diagnosing failures. For Intel, always assume modesetting; xf86-video-intel is deprecated and its SNA acceleration has its own bugs.

---

## 2. Wayland + amdgpu / Intel

### 2.1 EDID override via `drm.edid_firmware` — the universal mechanism

This is DRM-core functionality, **driver-agnostic**: it works with amdgpu, i915, and xe alike (xe reuses the same DRM connector/EDID infrastructure). Canonical workflow ([ArchWiki: Kernel mode setting](https://wiki.archlinux.org/title/Kernel_mode_setting), [foosel.net TIL](https://foosel.net/til/how-to-override-the-edid-data-of-a-monitor-under-linux/), [Monado EDID override doc](https://monado.freedesktop.org/edid-override.html), [dave jansen's GNOME/Wayland guide](https://davejansen.com/add-custom-resolution-and-refresh-rate-when-using-wayland-gnome/), [mcjmigdal's KDE Wayland gist](https://gist.github.com/mcjmigdal/3079ca80ad6b18bf077dcadc51563fac)):

```bash
# 1. Dump current EDID (connector names from /sys/class/drm/)
cat /sys/class/drm/card1-DP-1/edid > current.bin
edid-decode current.bin                      # from package edid-decode / v4l-utils

# 2. Edit (wxEDID, AW EDID Editor via Wine, or generate from a modeline — §6)

# 3. Install
sudo install -Dm644 modified.bin /usr/lib/firmware/edid/modified.bin

# 4. Kernel cmdline (GRUB: GRUB_CMDLINE_LINUX_DEFAULT, then grub-mkconfig)
drm.edid_firmware=DP-1:edid/modified.bin
# multiple displays: drm.edid_firmware=DP-1:edid/a.bin,HDMI-A-1:edid/b.bin

# 5. Early-KMS users MUST embed the file in the initramfs, or the override
#    silently fails at driver load:
#    mkinitcpio.conf:  MODULES=(amdgpu)  FILES=(/usr/lib/firmware/edid/modified.bin)
#    then: sudo mkinitcpio -P
#    dracut:  install_items+=" /usr/lib/firmware/edid/modified.bin "

# 6. Verify after reboot
dmesg | grep -i edid          # "Got external EDID base block ..."
edid-decode /sys/class/drm/card1-DP-1/edid
```

Version caveats:
- Parameter is `drm.edid_firmware=` since **kernel 4.15**; the older `drm_kms_helper.edid_firmware=` still works but prints a deprecation warning ([0xf8.org write-up](https://www.0xf8.org/2020/12/why-your-kernels-drm-edid_firmware-parameter-doesnt-work-anymore-in-libvirt-environments/), [LKML regression thread](https://lkml.iu.edu/hypermail/linux/kernel/1808.1/04722.html)). The kevinlekiller Intel guide still shows the old spelling — normalize to `drm.edid_firmware`.
- The kernel also ships built-in stock EDIDs (`edid/1920x1080.bin` etc., see `Documentation/admin-guide/edid.rst`) usable without any file when you just need a sane generic mode.
- **Runtime alternative (no reboot)** — DRM debugfs, ideal for a CRU-style "test before persist" flow (root required, debugfs mounted):

```bash
cat modified.bin | sudo tee /sys/kernel/debug/dri/1/DP-1/edid_override > /dev/null
echo 1 | sudo tee /sys/kernel/debug/dri/1/DP-1/trigger_hotplug
# revert: echo reset | sudo tee .../edid_override ; re-trigger hotplug
```

The compositor sees a hotplug and re-reads modes — new modes appear in KDE's/GNOME's display settings immediately. This is the mechanism the March 2026 amdgpu fix series (below) is hardening, so treat behavior as kernel-version-sensitive and always re-verify with `edid-decode`.

**HDMI vs DP quirks:**
- **amdgpu + HDMI**: until a March 2026 fix series (["drm/amd: fix HDMI signal type for EDID overrides"](https://lkml.iu.edu/hypermail/linux/kernel/2603.1/14378.html)), an override EDID whose CEA extension lacked a proper HDMI VSDB made DC treat the sink as **DVI** — consequences: no audio, 165 MHz TMDS cap, no 4:2:0. The fix makes DC trust the *physical connector type*. On older kernels the mitigation is on the tool: **when editing an EDID for an HDMI output, preserve the CTA-861 extension block, the HDMI VSDB, and the HF-VSDB (Max_TMDS_Character_Rate) — and raise Max_TMDS if overclocking past it.**
- **DP**: cleanest case; link bandwidth is validated against actual link training, not EDID, so an override can't unlock bandwidth the link doesn't have — it can only unlock *modes*. On **DSC monitors** (4K ≥120 Hz, 1440p ≥240 Hz), the native mode is only reachable *because of* DSC; a hand-built EDID that drops the DSC capability descriptors in the DisplayID/CTA blocks will make the native mode vanish or fall back to 4:2:0. Rule for the tool: start from the *dumped* EDID and modify minimally; never synthesize from scratch for DP 1.4+ panels.
- **VRR**: an override EDID missing the FreeSync range (AMD VSDB / DisplayID adaptive-sync block) kills VRR — observed as `vrr_capable = 0` on the connector ([NVIDIA forum case, same DRM-level principle](https://forums.developer.nvidia.com/t/overriding-edid-makes-vrr-stop-working-under-wayland-vrr-capable-immutable-range-0-1-0/302929)). Check after applying: `cat /sys/class/drm/card*-DP-1/vrr_capable`.
- **eDP (laptop panels)**: overrides work but i915 may still log `Custom mode despite valid EDID override` style complaints and clamp to panel-native limits from VBT ([Arch forums](https://bbs.archlinux.org/viewtopic.php?id=307336)).

**Intel-specific validation notes**: i915 caps pixel clock per-platform (CDCLK/port limits); some 4K@60+ monitors need BDB/VBT table version >230 support in newer kernels ([Ubuntu bug #1922372](https://bugs.launchpad.net/bugs/1922372)); i915 deliberately filters out-of-spec modes after bad EDIDs broke panels ([Ubuntu bug #1901470](https://bugs.launchpad.net/bugs/1901470)). No i915/xe module parameter bypasses this; historical monitor-overclocking on Haswell-era Intel involved kernel patches, but the maintained approach is EDID override within hardware limits ([kevinlekiller guide](https://github.com/kevinlekiller/linux_intel_display_overclocking)). On Intel HDMI via DP++ adapters, `max bpc` sometimes must be lowered to fit bandwidth: `xrandr --output DP-1 --set "max bpc" 8` (X11) or the equivalent connector property on Wayland compositors that expose it.

### 2.2 amdgpu module parameters relevant to modes/refresh

- **`amdgpu.freesync_video=1`** ([kernel docs](https://docs.kernel.org/gpu/amdgpu/module-parameters.html), [original patch series](https://patchwork.kernel.org/project/dri-devel/patch/20201214222036.561352-2-aurabindo.pillai@amd.com/), [Phoronix](https://www.phoronix.com/news/FreeSync-Exp-Video-Optimization)): on VRR-capable displays, DC **injects extra modes** at common video rates (24/25/30/48/50/60 Hz family) that differ from the base mode **only in vertical front porch**, all within the panel's VRR range. Switching between the base mode and these modes is **seamless — no blanking, no full modeset** (the driver just adjusts front porch, effectively fixed-rate VRR). Status: still marked experimental, default 0; it was removed in early 2024 and then **reverted/restored** after Steam Deck/user regressions ([revert thread](https://www.mail-archive.com/amd-gfx@lists.freedesktop.org/msg103436.html)) — it remains present and documented in current kernels (6.1x). Relevance to a CRU tool: with this flag, "add a 48 Hz variant of my 1440p mode" is already done by the driver, and your tool should de-duplicate these injected modes in its UI (they're flagged as driver-generated; on X11 they show up as additional `xrandr` modes with identical resolution).
- No DC parameter loosens bandwidth validation (see §1.2 for `dcdebugmask`/`dcfeaturemask`).

### 2.3 Compositor support matrix for custom modes (mid-2026)

| Compositor | Native custom modes? | Mechanism |
|---|---|---|
| **sway** (wlroots) | Yes, since 1.7 | `output DP-1 modeline 397.75 2560 2760 3040 3520 1440 1443 1448 1492 -hsync +vsync` or `output DP-1 mode --custom 2560x1440@75Hz` (DRM backend only) — [sway-output(5)](https://man.archlinux.org/man/sway-output.5), [wlroots PR #1095](https://github.com/swaywm/wlroots/pull/1095) |
| **Hyprland** | Yes | `monitor = DP-1, modeline 397.75 2560 2760 3040 3520 1440 1443 1448 1492 -hsync +vsync, 0x0, 1` — [PR #2254](https://github.com/hyprwm/Hyprland/pull/2254), [wiki](https://wiki.hypr.land/Configuring/Basics/Monitors/); wiki explicitly warns "might or might not work — it's all down to the driver" |
| **KWin / Plasma** | Landing now | [MR !8534 "backends/drm: support configuring custom modes"](https://invent.kde.org/plasma/kwin/-/merge_requests/8534) (opened Dec 2025, deps `plasma-wayland-protocols!121` + libkscreen merged; tracking [bug 456697](https://bugs.kde.org/show_bug.cgi?id=456697)); companion [MR !8766](https://invent.kde.org/plasma/kwin/-/merge_requests/8766) for virtual outputs targets **Plasma 6.6**. Caveat noted in the MR: adding a custom mode and switching to it are **two non-atomic steps**. Until it ships: EDID override only; mode *selection* via `kscreen-doctor output.DP-1.mode.2560x1440@75` |
| **GNOME / mutter** | No | Open feature request [mutter#2856](https://gitlab.gnome.org/GNOME/mutter/-/issues/2856); `monitors.xml` cannot add modes, only select existing ones, and doesn't apply to GDM on Wayland ([GNOME Discourse](https://discourse.gnome.org/t/wayland-gdm-add-custom-monitor-resolution/6104)). EDID override is the only path |

Design consequence: on Wayland your tool has **two backends** — (a) an EDID-override engine (universal, needs root, survives compositors), and (b) optional compositor-native adapters (sway/Hyprland config writers, KWin protocol once Plasma 6.6 is common, `kscreen-doctor` for mode selection today).

Also note the kernel `video=` parameter (`video=DP-1:2560x1440@75` with flags `R`=reduced blanking, `M`=CVT, `e`=force enable) — it can only express CVT/GTF-computed timings, **not arbitrary modelines**, and compositors are free to override it after startup; useful mainly for consoles/greeters ([ArchWiki: Kernel mode setting](https://wiki.archlinux.org/title/Kernel_mode_setting)).

---

## 3. Refresh-rate-only changes and VRR (brief)

- **X11**: `xrandr --output DP-1 --rate 75` (optionally with `--mode`) selects an *existing* mode with that rate — no validation drama since it's EDID-advertised. `xrandr --query` lists rates per mode.
- **VRR on X11** (both AMD and Intel): works only for a **single fullscreen application on the primary display**, no multi-monitor VRR. Enable via:
  ```
  Section "Device"
      Identifier "AMD"
      Driver "amdgpu"          # or "modesetting"
      Option "VariableRefresh" "true"
  EndSection
  ```
  xf86-video-amdgpu has supported it for years; the modesetting driver (Intel's only maintained DDX, also usable on AMD) since **xorg-server 21.1** ([ArchWiki: Variable refresh rate](https://wiki.archlinux.org/title/Variable_refresh_rate), [amdgpu(4)](https://manpages.debian.org/testing/xserver-xorg-video-amdgpu/amdgpu.4.en.html)).
- **VRR on Wayland**: KDE Plasma — per-output Adaptive Sync (Automatic/Always) in Display settings, works on amdgpu and Intel; sway — `output DP-1 adaptive_sync on`; Hyprland — `misc:vrr`; GNOME — experimental since 46 (`gsettings set org.gnome.mutter experimental-features "['variable-refresh-rate']"`), **promoted to non-experimental in GNOME 50** (still off by default per display) ([Phoronix](https://www.phoronix.com/news/GNOME-VRR-Not-Experimental), [mutter MR !1154](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/1154)).
- Interaction with this tool: EDID overrides must preserve VRR blocks (§2.1), and `amdgpu.freesync_video` mode injection means "refresh-only variants" may already exist without your tool creating them.

---

## 4. Monitor overclocking on AMD/Intel — what actually works (community recipes 2024–2026)

The consolidated recipe (from [kevinlekiller's guide](https://github.com/kevinlekiller/linux_intel_display_overclocking) — Intel-focused but the method is GPU-agnostic; [Arch forums 6700XT thread](https://bbs.archlinux.org/viewtopic.php?id=288460); [team-simple guide](https://team-simple.org/forum/viewtopic.php?id=6233); [CachyOS KDE Wayland thread](https://discuss.cachyos.org/t/how-do-i-set-a-custom-resolution-or-refresh-rate-kde-plasma-wayland/30610)):

1. **Compute headroom first.** Interface limits: HDMI 1.2 ≈165 MHz, HDMI 1.4 ≈340 MHz, HDMI 2.0 ≈600 MHz TMDS; DP depends on trained link (HBR2 4-lane ≈ 17.28 Gbps payload). The binding limit is often the *monitor's claimed* max (EDID max pixel clock ×10 MHz field, HDMI Max_TMDS) rather than the cable standard.
2. **Use reduced blanking** (cvt12 `-b` for RB2 at arbitrary refresh) to keep pixel clock down — this is the single biggest lever. 1080p: 60→75 Hz with RB2 is ~156 MHz, inside even single-link limits, which is why 1080p60→75 "just works" on most panels. 4K 120→144 without DSC needs ~1070+ MHz with RB2 — only feasible on DP HBR3/UHBR or HDMI 2.1 FRL paths, and on DSC monitors you must *keep* DSC (don't strip it from the EDID, don't set `dcdebugmask=0x4`).
3. **X11 path**: iterate with `xrandr --newmode/--addmode` at increasing rates (fast feedback, session-local, zero persistence risk). Known AMD quirks: flicker near the panel's real limit (frame skipping — verify with testufo-style skipped-frame test), and the `"scaling mode" "Full"` property fix.
4. **Wayland / persistence path**: bake the winning modeline into the EDID as the first DTD (and bump max pixel clock / Max TMDS fields), install via `drm.edid_firmware` (§2.1). This is the exact CRU-on-Windows equivalent and works on GNOME, KDE, everything. On sway/Hyprland you can skip EDID and use `modeline` config instead.
5. **Typical failure modes**:
   - *Monitor OSD "out of range" / blank screen* — timings exceed panel; revert (see §5). The classic self-reverting test one-liner: `xrandr --output HDMI-1 --mode "test" && sleep 5 && xrandr --output HDMI-1 --mode "1920x1080"` ([team-simple](https://team-simple.org/forum/viewtopic.php?id=6233)).
   - *Kernel rejects commit* (`Configure crtc failed`, dmesg bandwidth errors) — pixel clock over interface/EDID limit; lower blanking or raise EDID limits.
   - *Panel accepts but skips frames* — visually "works", actually drops every Nth frame; the tool should offer a frame-skip test pattern.
   - *Silent clamp to 4:2:0/4:2:2* on HDMI (DC's automatic encoding downgrade) — text fringing; detect via `/sys/kernel/debug/dri/*/…/output_bpc` or DC state in debugfs.
   - *VRR/audio lost after EDID override* — mangled CTA blocks (§2.1).
   - *Boot-time override + broken EDID = blank from initramfs onward* — see recovery below and [LibreELEC dual-boot case](https://forum.libreelec.tv/thread/28033-custom-edid-on-dual-boot-system-black-screen-on-boot/).

---

## 5. Safety & recovery — how the tool should test-and-revert

**X11 (straightforward — replicate what DEs do):**
1. Snapshot current CRTC config (`XRRGetCrtcInfo` or parse `xrandr --verbose`).
2. Apply the candidate mode.
3. Show a confirm dialog with a countdown; on timeout or Esc, restore the snapshot. X11 keeps processing input even if the monitor shows nothing, so a blind revert always works. Precedents: **KDE reverts after 15 s** (hardcoded `revertCountdown = 15`, 1 s `Timer` in [kscreen kcm/ui/main.qml](https://github.com/KDE/kscreen/blob/master/kcm/ui/main.qml) — "Will revert to previous configuration in %1 second(s)"), **GNOME after 20 s** ([openSUSE GNOME docs](https://doc.opensuse.org/documentation/leap/archive/42.2/gnomeuser/html/book.gnomeuser/cha.gnome.settings.html)), XFCE 10 s. Use a keyboard-confirmable dialog (Enter=keep) since the screen may be black; timeout-only revert is the standard.
4. Extra belt: write a `at`/systemd-run fallback (`systemd-run --user --on-active=20s xrandr --output DP-1 --mode <safe>`) that the tool cancels on confirm — survives a tool crash mid-test.

**Wayland:**
- Good news first: compositors use **atomic TEST_ONLY commits**, so any mode the *driver* would reject fails cleanly before reaching the display — the only real risk is the *monitor* rejecting valid-to-the-GPU timings.
- If driving modes through the compositor (kscreen-doctor / sway IPC / Hyprland `hyprctl keyword monitor ...`), implement the same snapshot→apply→countdown→revert loop through the same API. KDE's own KCM already provides its 15 s revert when the user applies via System Settings; `kscreen-doctor` applies **without** any revert dialog, so your tool must supply its own timer. sway/Hyprland: revert = re-issue previous output command; both revert to the last working state on a failed DRM commit anyway.
- **EDID override rollback strategy** (the dangerous persistent path):
  1. *Always test at runtime first* via debugfs `edid_override` + `trigger_hotplug` (§2.1) before touching the kernel cmdline.
  2. When persisting, **keep a fallback boot entry without `drm.edid_firmware`** (or instruct: hold Shift/Esc for GRUB, press `e`, delete the parameter — this is the universal recovery, cite it in the UI).
  3. Never mark the modified entry as the only one; on systemd-boot write a separate `.conf`.
  4. `nomodeset` as last-resort rescue boots to an unaccelerated console.
- **Compositor config rollback**: snapshot and restore `~/.config/kwinoutputconfig.json` (Plasma 6) / `~/.local/share/kscreen/*` (Plasma 5), `~/.config/monitors.xml` (GNOME — note GDM has its own copy in `/var/lib/gdm/.config/`), the relevant `output` lines in sway/Hyprland configs ([techienotes reset guide](https://techienotes.blog/2020/03/24/how-to-reset-display-settings-for-kde-plasma/), [openSUSE forums Plasma 6 reset](https://forums.opensuse.org/t/how-to-reset-second-monitor-settings-plasma-6-wayland/188394)).

---

## 6. Existing "CRU-like" tooling on Linux (prior art to reuse or beat)

- **wxEDID** — the closest thing to CRU's EDID editor on Linux: GUI editor for EDID 1.3+/CTA-861-G with a DTD constructor; packaged on [Flathub](https://flathub.org/en/apps/net.sourceforge.wxEDID). No apply/persist logic — editing only.
- **AW EDID Editor** (Analog Way, gratis Windows app, runs under Wine) — recommended by the kevinlekiller guide for block-aware editing.
- **CRU itself (ToastyX)** — Windows-only registry overrides ([monitortests forum](https://www.monitortests.com/forum/Thread-Custom-Resolution-Utility-CRU)); a common Linux workflow is: run CRU in a Windows VM/dual-boot → export the modified EDID → load via `drm.edid_firmware`. Your tool eliminates this round-trip.
- **edid-generator** ([akatrevorjay/edid-generator](https://github.com/akatrevorjay/edid-generator)) — shell/Makefile around the kernel's `Documentation/admin-guide/edid` assembly sources: **modeline in → EDID .bin out**. This is the key missing primitive for a GUI (modeline→EDID synthesis) and is directly embeddable.
- **edid-decode** (v4l-utils) and **read-edid/get-edid** — parsing/validation; shell out or reimplement; `edid-decode --check` catches checksum/structure errors before you let a user flash a broken blob.
- **kscreen-doctor** (KDE) / **gnome-randr-rust** / **wlr-randr** — Wayland mode *selection* CLIs (no mode creation), useful as apply-backends.
- Recipe gists this tool essentially automates: [KDE Wayland EDID injection](https://gist.github.com/mcjmigdal/3079ca80ad6b18bf077dcadc51563fac), [Wayland custom resolution](https://gist.github.com/szymonwilczek/b3893d11d4b4927d2923badd9f141d06), [GNOME Wayland custom modes](https://davejansen.com/add-custom-resolution-and-refresh-rate-when-using-wayland-gnome/), [Monado EDID override](https://monado.freedesktop.org/edid-override.html).

**Gap analysis**: nothing on Linux today combines (modeline calculator → EDID patcher that *preserves* CTA/DSC/VRR blocks → runtime debugfs test with auto-revert → persistence via kernel param or compositor config). Every piece exists separately; that integration is exactly the niche for this project, and on AMD/Intel it needs no driver-specific hacks — only the DRM-standard mechanisms documented above.
