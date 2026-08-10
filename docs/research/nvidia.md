# Custom Resolutions & Refresh Rates on NVIDIA GPUs under Linux — Technical Report (2025–2026)

**Scope:** proprietary driver era 550–580+ (newest published branch at time of writing: 610.57.04 on [download.nvidia.com](https://download.nvidia.com/XFree86/Linux-x86_64/)), open GPU kernel modules, nouveau. Focus: what a Linux CRU-style GUI can and cannot do.

**TL;DR for the tool:**
- **X11 + NVIDIA:** xorg.conf `Modeline` + `Option "ModeValidation"` remains fully supported and is the canonical path. `xrandr --newmode/--addmode` *does* work, but only after `AllowNonEdidModes` (and often pixel-clock flags) are set in xorg.conf — so a config write + X restart is required anyway.
- **Wayland + NVIDIA:** the *only* working mechanism is an EDID override through the DRM core (`drm.edid_firmware=` kernel parameter or debugfs `edid_override`), which the NVIDIA driver honors **since driver 535.43.02 on kernels ≥ 6.2**. Compositor modelines, `video=`, and any "relax validation" knob do **not** work — there is no Wayland equivalent of `ModeValidation`.
- **nouveau:** standard DRM driver; everything standard works.

---

## 1. X11 + NVIDIA proprietary driver

### 1.1 Modelines + `Option "ModeValidation"`

The NVIDIA X driver ignores/discards modes that fail its internal validation. By default, on digital outputs (DFPs) **only EDID-advertised modes are allowed**; a custom `Modeline` in the `Monitor` section is silently dropped unless validation is relaxed. This is unchanged in the 550–610 era.

Canonical working config (verified structure per NVIDIA README and Arch wiki):

```conf
Section "Monitor"
    Identifier  "Monitor0"
    # 1920x1080 @ 100 Hz CVT-RB example
    Modeline "1920x1080_100" 235.50 1920 1968 2000 2080 1080 1083 1088 1133 +hsync -vsync
EndSection

Section "Device"
    Identifier  "Device0"
    Driver      "nvidia"
    Option      "ModeValidation" "AllowNonEdidModes, NoMaxPClkCheck, NoEdidMaxPClkCheck"
    # per-output form:
    # Option "ModeValidation" "DP-0: AllowNonEdidModes, NoMaxPClkCheck; HDMI-0: NoEdidModes"
    Option      "ModeDebug" "true"     # verbose per-mode validation results in Xorg.0.log
EndSection

Section "Screen"
    Identifier  "Screen0"
    Device      "Device0"
    Monitor     "Monitor0"
    Option      "MetaModes" "DP-0: 1920x1080_100 +0+0"
EndSection
```

**Full `ModeValidation` token list — extracted directly from the 580.178.04 README** ([xconfigoptions.html](https://download.nvidia.com/XFree86/Linux-x86_64/580.178.04/README/xconfigoptions.html)):

| Token | Effect |
|---|---|
| `AllowNonEdidModes` | Allow modes not present in the EDID on digital outputs — **the key flag for custom modes** |
| `NoMaxPClkCheck` | Skip hardware max pixel-clock check |
| `NoEdidMaxPClkCheck` | Skip EDID-declared max pixel-clock check (refresh overclocking) |
| `NoHorizSyncCheck` / `NoVertRefreshCheck` | Skip HorizSync/VertRefresh range checks |
| `NoMaxSizeCheck` | Skip GPU max resolution check |
| `NoVirtualSizeCheck` | Allow modes larger than the virtual screen |
| `NoTotalSizeCheck` | Allow timings exceeding raster size |
| `NoExtendedGpuCapabilitiesCheck` | Allow timings exceeding GPU capability tables |
| `NoDualLinkDVICheck` | Skip dual-link DVI checks |
| `NoDisplayPortBandwidthCheck` | Skip DP link-bandwidth check |
| `NoEdidHDMI2Check` | Allow HDMI-2.0 4K@60 RGB 4:4:4 without EDID support |
| `NoVesaModes` / `NoEdidModes` / `NoXServerModes` / `NoCustomModes` / `NoPredefinedModes` / `NoUserModes` | Remove entire mode-source pools (`NoUserModes` blocks NV-CONTROL/RandR user-added modes) |
| `ObeyEdidContradictions` | Re-enable strict EDID-contradiction rejection |
| `AllowNon3DVisionModes`, `AllowNonHDMI3DModes`, `AllowDpInterlaced`, `NoInterlacedModes`, `MaxOneHardwareHead`, `PreferHDMIFrlMode` | Niche flags (3D Vision, interlace, dual-head-per-mode, HDMI FRL vs TMDS preference) |

**Flags that no longer exist:** `NoDFPNativeResolutionCheck` and `NoEdidDFPMaxSizeCheck` are **gone** from the 580 README (they existed in 3xx-era drivers). `AllowGsyncOnAllDisplays` is **not** a ModeValidation token and never was in this era — G-SYNC forcing is a *MetaMode attribute* (`AllowGSYNCCompatible=On`, see 1.3). The GUI should not emit the removed tokens.

**Which flags are actually needed in practice:**
- Plain custom resolution within monitor limits: `AllowNonEdidModes` only.
- Refresh-rate overclocking: add `NoEdidMaxPClkCheck` (and `NoMaxPClkCheck` if you exceed the link/hardware table, e.g. HDMI TMDS 165/340 MHz clamps — see [Arch BBS #299812](https://bbs.archlinux.org/viewtopic.php?id=299812), where a GTX 1650S over HDMI was clamped to 165 MHz pclk, 560.35.03).
- Out-of-range sync monitors: `NoHorizSyncCheck, NoVertRefreshCheck` or explicit `HorizSync`/`VertRefresh` in the Monitor section with `Option "UseEdidFreqs" "false"`.
- `NoDisplayPortBandwidthCheck` / `NoExtendedGpuCapabilitiesCheck` only as user-selectable "unsafe" extras.

Related: `Option "ExactModeTimingsDVI" "true"` forces exact modeline timings instead of snapping to the closest EDID mode; `AllowNonEdidModes` is the modern per-device-granular equivalent (README, Appendix B). `Option "ModeDebug" "true"` is what the GUI should enable to parse *why* a mode was rejected from `Xorg.0.log`.

Sources: [NVIDIA 580.178.04 README App. B](https://download.nvidia.com/XFree86/Linux-x86_64/580.178.04/README/xconfigoptions.html), [ArchWiki NVIDIA/Troubleshooting](https://wiki.archlinux.org/title/NVIDIA/Troubleshooting), [blogshit.baka.fi — Xorg, Nvidia and Custom Resolutions](https://blogshit.baka.fi/2020/07/xorg-custom-resolutions/), [FS-UAE 50 Hz guide](https://fs-uae.net/50hz-display-modes-on-linux-with-nvidia-drivers/).

### 1.2 `xrandr --newmode` / `--addmode` status (current)

Still the same behavior in 550–580: `--newmode` succeeds (it only registers a mode with the X server), but `--addmode` fails with `X Error … BadMatch` because the NVIDIA driver validates the mode against EDID/pclk limits at attach time. **It is not "rejected always" — it works once `ModeValidation` is relaxed in xorg.conf** (`AllowNonEdidModes`, plus pclk flags as needed). This is confirmed by NVIDIA staff on the Jetson/dGPU forums ("on DFPs only EDID-advertised modes are allowed; add `AllowNonEdidModes`") and by the Arch wiki's "xrandr BadMatch" section.

Consequence for the GUI: pure runtime xrandr injection is impossible on a stock config; you must write the `ModeValidation` option and restart X first. After that, `xrandr --newmode ... && xrandr --addmode DP-0 ...` works and can be used for live testing before persisting a Modeline.

Sources: [NVIDIA forums — BadMatch on Orin/xrandr](https://forums.developer.nvidia.com/t/cannot-set-custom-resolution-via-xrandr-on-jetson-orin-nano-badmatch-error/347219), [Custom resolution not working](https://forums.developer.nvidia.com/t/custom-resolution-not-working/188796), [ArchWiki NVIDIA/Troubleshooting — xrandr BadMatch](https://wiki.archlinux.org/title/NVIDIA/Troubleshooting), [Arch BBS #255287](https://bbs.archlinux.org/viewtopic.php?id=255287).

### 1.3 nvidia-settings

- **There is no modeline/"advanced timing" editor in Linux nvidia-settings** — that dialog ("Custom Resolutions… > Timing") exists only in the Windows NVIDIA Control Panel. On Linux, nvidia-settings can only *select and arrange* modes that already passed validation. A CRU-like GUI fills exactly this gap.
- **MetaModes** are the driver's native multi-display mode description. Query and assign at runtime over NV-CONTROL:
  ```bash
  nvidia-settings -q CurrentMetaMode
  nvidia-settings --assign CurrentMetaMode="DP-0: 1920x1080_100 +0+0 {AllowGSYNCCompatible=On}"
  ```
  Custom modelines defined in the Monitor section are referenced **by name** in MetaModes (as above). Useful MetaMode attributes: `ViewPortIn`, `ViewPortOut` (overscan/scaling), `ForceCompositionPipeline`, `ForceFullCompositionPipeline`, `AllowGSYNC` / `AllowGSYNCCompatible`, `VRRMinRefreshRate`, `OutputBitsPerComponent`.
- nvidia-settings talks to the **X driver only** (NV-CONTROL). On a Wayland session it is largely non-functional (only a few pages appear); none of the mode machinery is available there.

Sources: [580.178.04 README Ch. 12 MetaModes](https://download.nvidia.com/XFree86/Linux-x86_64/580.178.04/README/configtwinview.html), [nvidia-settings man page](https://linuxcommandlibrary.com/man/nvidia-settings), [Windows-only Custom Timings dialog](https://www.nvidia.com/content/Control-Panel-Help/vLatest/en-gb/mergedProjects/nvdspENG/Custom_Timings_Dialog_Box.htm).

### 1.4 `UseEDID`, `CustomEDID`, `IgnoreEDID`

- `Option "UseEDID" "false"` — master switch; disables EDID for **mode sourcing, HorizSync/VertRefresh inference (`UseEdidFreqs`), and DPI (`UseEdidDpi`)** all at once. You must then supply `HorizSync`/`VertRefresh` in the Monitor section, and usually `ConnectedMonitor` if detection depends on DDC. The three sub-options can be disabled individually instead.
- `Option "CustomEDID" "DFP-0:/etc/X11/edid.bin; DP-1:/etc/X11/edid2.bin"` — the driver behaves *exactly as if that EDID was read from the monitor* (semicolon-separated per-output list; generic class names `CRT`/`DFP`/`TV` and `GPU-0.` qualifiers accepted). This is the X11 equivalent of Windows CRU: a CRU-exported/edited `.bin` fed here gives you the custom modes with **no ModeValidation flags needed**, since the modes are now "in EDID". Verified working in the 590-era on X11 ([Arch BBS #311326](https://bbs.archlinux.org/viewtopic.php?id=311326), Dec 2025).
- **`Option "IgnoreEDID"` is long removed** — it does not exist in the 580 README (only `IgnoreEDIDChecksum` remains, for accepting corrupt-checksum EDIDs, e.g. `Option "IgnoreEDIDChecksum" "DFP-0"`). Use `UseEDID`.
- **Connector naming (X side):** type-based `DFP-n`/`CRT-n`/`TV-n`, connector-based `DP-n`, `HDMI-n`, `DVI-I-n`, `eDP-n` (these are also the RandR output names), EDID-hash names `DPY-EDID-<uuid>`, and `GPU-x.` prefixes. **Warning for the tool:** NVIDIA X names do *not* match kernel DRM names — X `DP-0` vs kernel `DP-1`/`card1-DP-1` (DRM indexes per-card starting at 1, and HDMI is `HDMI-A-n` in DRM vs `HDMI-n` in X). Maintain two separate name maps.

Sources: [580.178.04 README App. B](https://download.nvidia.com/XFree86/Linux-x86_64/580.178.04/README/xconfigoptions.html), [App. C Display Device Names](https://download.nvidia.com/XFree86/Linux-x86_64/580.178.04/README/displaydevicenames.html).

---

## 2. Wayland + NVIDIA proprietary driver

### 2.1 Prerequisites

`nvidia-drm.modeset=1` is mandatory for any Wayland session. Since the **560 series** (and distro packaging like Arch `nvidia-utils 560.35.03-5`), `modeset` and `fbdev` **default to 1**; before 560 they had to be set via kernel cmdline or `/etc/modprobe.d`. `nvidia-drm.fbdev=1` only affects the framebuffer console, not custom-mode capability. Sources: [ArchWiki NVIDIA](https://wiki.archlinux.org/title/NVIDIA), [korvahannu guide issue #33](https://github.com/korvahannu/arch-nvidia-drivers-installation-guide/issues/33), [open-gpu-kernel-modules #1163](https://github.com/NVIDIA/open-gpu-kernel-modules/issues/1163).

### 2.2 `drm.edid_firmware` — the answer changed, and here is the proof

**Historically NO, currently YES (with conditions).** Verified at source level in `kernel-open/nvidia-drm/nvidia-drm-connector.c` ([GitHub, main branch](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/main/kernel-open/nvidia-drm/nvidia-drm-connector.c)):

```c
#if defined(NV_DRM_CONNECTOR_HAS_OVERRIDE_EDID)
    if (connector->override_edid) {
#else
    if (drm_edid_override_connector_update(connector) > 0) {
#endif
        const struct drm_property_blob *edid = connector->edid_blob_ptr;
        ...
        pDetectParams->overrideEdid = NV_TRUE;
```

The override EDID is copied into the detect parameters and passed down into NVKMS, which then builds and validates its mode list from it. Version history (checked across release tags):

- **515.43.04–525.x:** only the `connector->override_edid` flag path exists — i.e. only the **debugfs** `edid_override` mechanism was honored; the `drm.edid_firmware=` cmdline was ignored. This is exactly what users reported in Oct 2022 ([NVIDIA forum #229658](https://forums.developer.nvidia.com/t/nvidia-driver-ignoring-custom-edid-using-drm-edid-firmware/229658)).
- **535.43.02 and all later branches (545/550/555/560/570/580):** fallback to `drm_edid_override_connector_update()` was added. On **kernel ≥ 6.2** (where `override_edid` was removed and DRM core routes both debugfs overrides *and* the `edid_firmware` parameter through that function), **`drm.edid_firmware=` works with the proprietary driver**.

So the practical matrix: **driver ≥ 535 + kernel ≥ 6.2 + `nvidia-drm.modeset=1` → `drm.edid_firmware=DP-1:edid/custom.bin` is honored**, and this is the mechanism behind every successful 2025 recipe ([szymonwilczek gist — KDE Wayland + Hyprland on Arch, NVIDIA proprietary](https://gist.github.com/szymonwilczek/b3893d11d4b4927d2923badd9f141d06), [marek-g write-up](https://marek-g.github.io/posts/tips_and_tricks/wayland_custom_resolution/), [Garuda forum "Overclock Refresh Rate in Wayland"](https://forum.garudalinux.org/t/overclock-refresh-rate-in-wayland/26023)). Note it applies at **connector detect time** — the EDID must be available when nvidia-drm probes (put it in the initramfs if KMS starts early), and post-boot changes need a replug or `trigger_hotplug`.

**Caveats (all with sources):**
- **VRR/G-SYNC breaks when EDID is overridden on Wayland**: the driver reports `vrr_capable = 0` even when overriding the stock EDID with itself. NVIDIA filed internal bug 4797139 (staff "amrits", Aug 2024); still reproducing at 570.86.16 (Feb 2025). Same EDID via X11 `CustomEDID` keeps VRR. [NVIDIA forum #302929](https://forums.developer.nvidia.com/t/overriding-edid-makes-vrr-stop-working-under-wayland-vrr-capable-immutable-range-0-1-0/302929), [KDE bug 491411](https://bugs.kde.org/show_bug.cgi?id=491411).
- **DSC:** the driver ignores EDID overrides if DSC is active and the max resolution/refresh combination exceeds the single-head pixel-clock limit ([same thread, p.2](https://forums.developer.nvidia.com/t/overriding-edid-makes-vrr-stop-working-under-wayland-vrr-capable-immutable-range-0-1-0/302929?page=2); the szymonwilczek gist comments echo DSC problems above 120 Hz).
- **The EDID content is still validated** — NVKMS rejects malformed EDIDs ("The EDID has a bad detailed timing descriptor", [Arch BBS #311326](https://bbs.archlinux.org/viewtopic.php?id=311326)), and on HDMI the mode list is still clamped (~165 MHz pclk) unless the EDID itself carries an HDMI VSDB/HF-VSDB declaring higher max-TMDS/FRL — the tool must write those blocks, not just a DTD ([HarryAnkers virtual-display gist](https://gist.github.com/HarryAnkers/8dbf551d66f00e8156ef4dd2b2b090a0)).
- **Still not 100 % reliable:** a Dec 2025 report on 580.105.08 (RTX 2060S, Fedora 43) shows an override not taking effect while nouveau handled the same EDID fine ([NVIDIA forum #353266](https://forums.developer.nvidia.com/t/wayland-nvidia-only-exposes-1920x1080-on-lenovo-thinkvision-p27h-20-even-though-edid-provides-2560x1440-and-edid-override-is-present-nouveau-sam/353266)).

**Known-working recipe (what the GUI should automate for Wayland):**

```bash
# 1. Get current EDID
cat /sys/class/drm/card1-DP-1/edid > mon.bin
# 2. Edit it (add DTD / DisplayID block, fix ranges, keep checksums valid)
# 3. Install
sudo mkdir -p /usr/lib/firmware/edid && sudo cp custom.bin /usr/lib/firmware/edid/
# 4. Kernel cmdline (kernel DRM connector names, not X names!)
#    drm.edid_firmware=DP-1:edid/custom.bin,HDMI-A-1:edid/other.bin
# 5. Embed in initramfs (needed with early KMS):
#    mkinitcpio: FILES=(/usr/lib/firmware/edid/custom.bin)
#    dracut:     install_items+=" /usr/lib/firmware/edid/custom.bin "
# 6. Reboot; verify with: edid-decode < /sys/class/drm/card1-DP-1/edid
```

Live-test path without reboot (root, **fails under Secure Boot/kernel lockdown** since debugfs is restricted):

```bash
cat custom.bin > /sys/kernel/debug/dri/0/DP-1/edid_override
echo 1 > /sys/kernel/debug/dri/0/DP-1/trigger_hotplug   # re-detect without replug
echo -n reset > /sys/kernel/debug/dri/0/DP-1/edid_override  # undo
```

Sources: [ArchWiki Kernel mode setting — Forcing modes and EDID](https://wiki.archlinux.org/title/Kernel_mode_setting), [mcjmigdal gist (debugfs on KDE Wayland)](https://gist.github.com/mcjmigdal/3079ca80ad6b18bf077dcadc51563fac), gists above.

### 2.3 `video=` kernel parameter

**Does not work for custom modes on nvidia-drm.** Mechanism: nvidia-drm uses `drm_helper_probe_single_connector_modes` as its `fill_modes`, so DRM core *does* parse `video=` and adds the cmdline mode — but the helper then calls the driver's `mode_valid`, which is `nv_drm_connector_mode_valid()` → `nvKms->validateDisplayMode()`, and NVKMS rejects any mode not derivable from the (possibly overridden) EDID. Community reports match: `video=` custom modes are rejected/ignored, and `video=…:e` force-enable yields a connector with no usable modes. The EDID override is the supported input; `video=` is not. Sources: [nvidia-drm-connector.c](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/main/kernel-open/nvidia-drm/nvidia-drm-connector.c) (code inspection), [szymonwilczek gist ("video= rejected by the NVIDIA driver")](https://gist.github.com/szymonwilczek/b3893d11d4b4927d2923badd9f141d06), [HarryAnkers gist](https://gist.github.com/HarryAnkers/8dbf551d66f00e8156ef4dd2b2b090a0), [ArchWiki KMS "Forcing modes"](https://wiki.archlinux.org/title/Kernel_mode_setting).

### 2.4 Module options / registry keys

There is **no NVKMS/`nvidia-modeset` module parameter or `NVreg_RegistryDwords` key that relaxes mode validation or overrides EDID** — nothing equivalent to `ModeValidation` exists outside the X driver. The documented nvidia-drm parameters are `modeset` and `fbdev` only; NVKMS parameters cover console/malloc/vblank knobs, none mode-validation related. Requests for a Wayland-side ModeValidation are open feature requests on the NVIDIA forums with no implementation as of the 580/595 READMEs ([Features the Nvidia Linux driver is missing](https://forums.developer.nvidia.com/t/features-the-nvidia-linux-driver-is-missing-vs-others-please-implement/182686), [Monitor overclocking in Linux is not available](https://forums.developer.nvidia.com/t/monitor-refresh-frequency-overclocking-in-linux-is-not-available/75756), [595.58.03 README DRM KMS chapter](https://download.nvidia.com/XFree86/Linux-x86_64/595.58.03/README/kms.html)).

### 2.5 Compositor-level workarounds

All Wayland compositors can only issue atomic commits; every mode goes through nvidia-drm's `mode_valid`/atomic check → NVKMS validation. Therefore:

- **Hyprland** has `modeline=` config support ([PR #2254](https://github.com/hyprwm/Hyprland/pull/2254), [issue #3143](https://github.com/hyprwm/Hyprland/issues/3143)); the [Hyprland wiki](https://wiki.hypr.land/Nvidia/) itself says custom modelines "might or might not work — it's all down to the driver". On the NVIDIA proprietary driver they are rejected unless the timing already matches an EDID/validated mode. The documented NVIDIA workaround in the community is the EDID override, not the modeline.
- **Sway/wlroots** `output <name> mode --custom WxH@RHz` — same story: it can select unlisted *resolutions* that NVKMS can scale/derive, but genuinely custom timings are refused by the driver.
- **KDE/GNOME:** no custom-mode creation UI at all; KScreen/mutter only pick from the driver's list. KDE tracks the NVIDIA EDID-override VRR regression as [bug 491411](https://bugs.kde.org/show_bug.cgi?id=491411).

**Conclusion: on NVIDIA Wayland the compositor is a dead end; the EDID override is the only lever.** ([Garuda forum recipe](https://forum.garudalinux.org/t/overclock-refresh-rate-in-wayland/26023), [Arch BBS #249045](https://bbs.archlinux.org/viewtopic.php?id=249045))

### 2.6 Open GPU kernel modules / GSP

- `nvidia-drm.ko` is built from the **same source** in both the proprietary and open packages, so everything in 2.2–2.4 applies identically. Mode validation lives in NVKMS/GSP-RM either way; GSP firmware offload does not add or remove any custom-mode capability — there is no GSP-level EDID knob.
- Open modules are the **default for Turing+ since the 560 release** ([Phoronix](https://www.phoronix.com/news/NVIDIA-560.31.02-Linux-Driver)).
- Open-modules-specific bug: EDID override via `/sys/module/drm/parameters/edid_firmware` producing image corruption on HDMI at 550.90.07, not reproducible on the closed kernel modules — [open-gpu-kernel-modules issue #668](https://github.com/NVIDIA/open-gpu-kernel-modules/issues/668) (still open, no NVIDIA response).
- No open issue/PR adds a mode-validation-bypass; the repo does not accept functional community patches to NVKMS policy.

---

## 3. nouveau / NVK (brief)

Confirmed: nouveau is a standard in-tree DRM/KMS driver, so **all generic mechanisms work normally** — `drm.edid_firmware=`, `video=` (including forcing timings), debugfs `edid_override`, and `xrandr --newmode/--addmode` under X11 (modesetting/nouveau DDX imposes no NVIDIA-style EDID policing). Extra: `nouveau.hdmimhz=297` (or 330) raises the HDMI pixel-clock limit ([ArchWiki NVIDIA/Troubleshooting](https://wiki.archlinux.org/title/NVIDIA/Troubleshooting), [ArchWiki KMS](https://wiki.archlinux.org/title/Kernel_mode_setting), [nouveau KMS wiki](https://nouveau.freedesktop.org/wiki/KernelModeSetting)). NVK is only the Vulkan userspace driver on top of nouveau KMS — irrelevant to modesetting; the Dec 2025 forum thread above even shows nouveau succeeding with an EDID that the proprietary driver mishandled. Caveat for the GUI: no reclocking on most GPUs → fine for custom modes, poor for gaming.

## 4. What CRU-on-Windows users are actually told to do on Linux (2024–2026 consensus)

Collected from Arch BBS, NVIDIA forums, Garuda forum, and the widely-shared gists:

1. **X11 + NVIDIA, modes within monitor spec:** xorg.conf `Modeline` + `ModeValidation "AllowNonEdidModes"` (+ pclk flags for overclocks), select via `xrandr` or `nvidia-settings` MetaModes. ([ArchWiki](https://wiki.archlinux.org/title/NVIDIA/Troubleshooting), [blogshit.baka.fi](https://blogshit.baka.fi/2020/07/xorg-custom-resolutions/))
2. **X11 + NVIDIA, "just like CRU":** run actual CRU in Windows (or edit the EDID with wxEDID/edid-decode on Linux), export `.bin`, feed via `Option "CustomEDID" "DP-0:/path/edid.bin"`. Keeps VRR working. ([Arch BBS #311326](https://bbs.archlinux.org/viewtopic.php?id=311326))
3. **Wayland + NVIDIA (the 2025 standard recipe):** CRU-edited or hand-patched EDID → `/usr/lib/firmware/edid/` → `drm.edid_firmware=<DRM-connector>:edid/file.bin` + `nvidia-drm.modeset=1` → rebuild initramfs → reboot. Works on KDE Plasma Wayland and Hyprland with 550/570/580 drivers, kernels 6.6+. ([szymonwilczek gist](https://gist.github.com/szymonwilczek/b3893d11d4b4927d2923badd9f141d06), [marek-g](https://marek-g.github.io/posts/tips_and_tricks/wayland_custom_resolution/), [Garuda](https://forum.garudalinux.org/t/overclock-refresh-rate-in-wayland/26023))
4. **Wayland quick test / no reboot:** debugfs `edid_override` + `trigger_hotplug` (root, no lockdown). ([mcjmigdal gist](https://gist.github.com/mcjmigdal/3079ca80ad6b18bf077dcadc51563fac), [NVIDIA forum #229658](https://forums.developer.nvidia.com/t/nvidia-driver-ignoring-custom-edid-using-drm-edid-firmware/229658))
5. Users who need VRR + custom modes together on Wayland are told: **stay on X11** or wait for NVIDIA bug 4797139.

## 5. Explicitly IMPOSSIBLE on NVIDIA + Wayland today

1. **Runtime injection of arbitrary modelines** (xrandr/NV-CONTROL equivalent) — no protocol, no tool; nvidia-settings is X-only. ([forum #75756](https://forums.developer.nvidia.com/t/monitor-refresh-frequency-overclocking-in-linux-is-not-available/75756))
2. **Relaxing mode validation** — `ModeValidation` has no NVKMS/module-parameter/Wayland counterpart; NVKMS rejects all non-EDID timings unconditionally. ([595 README KMS ch.](https://download.nvidia.com/XFree86/Linux-x86_64/595.58.03/README/kms.html), code inspection of `nv_drm_connector_mode_valid`)
3. **`video=` custom timings** — added by DRM core, then pruned by NVKMS validation. ([szymonwilczek gist](https://gist.github.com/szymonwilczek/b3893d11d4b4927d2923badd9f141d06), code inspection)
4. **Compositor custom modelines** (sway `--custom` timings, Hyprland `modeline`) — rejected by the driver's validation. ([Hyprland wiki Nvidia page](https://wiki.hypr.land/Nvidia/), [issue #3143](https://github.com/hyprwm/Hyprland/issues/3143))
5. **EDID override with working VRR/G-SYNC** — driver forces `vrr_capable=0`; unfixed from 560.31.02 through at least 570.86.16 (bug 4797139). ([forum #302929](https://forums.developer.nvidia.com/t/overriding-edid-makes-vrr-stop-working-under-wayland-vrr-capable-immutable-range-0-1-0/302929), [KDE bug 491411](https://bugs.kde.org/show_bug.cgi?id=491411))
6. **EDID override under active DSC beyond the single-head pclk limit** — silently ignored. ([forum #302929 p.2](https://forums.developer.nvidia.com/t/overriding-edid-makes-vrr-stop-working-under-wayland-vrr-capable-immutable-range-0-1-0/302929?page=2))
7. **debugfs live-testing under Secure Boot/lockdown** — debugfs writes blocked. ([ArchWiki KMS](https://wiki.archlinux.org/title/Kernel_mode_setting))
8. On **driver < 535 or kernel < 6.2**: `drm.edid_firmware` entirely ignored (only debugfs worked ≥ 515). ([forum #229658](https://forums.developer.nvidia.com/t/nvidia-driver-ignoring-custom-edid-using-drm-edid-firmware/229658), tag-by-tag source diff of `nvidia-drm-connector.c`)

## 6. Design implications for the linux-cru GUI

1. Treat **X11-NVIDIA** and **Wayland-NVIDIA** as two backends: (a) Modeline+ModeValidation writer (current code) plus optional `CustomEDID`; (b) an **EDID editor/patcher pipeline** — read `/sys/class/drm/*/edid`, inject DTDs/DisplayID/HF-VSDB, write to `/usr/lib/firmware/edid/`, edit kernel cmdline (`drm.edid_firmware=`), hook initramfs regeneration, and offer a debugfs+`trigger_hotplug` "Test" button with lockdown detection.
2. Gate the Wayland backend on `driver ≥ 535`, `kernel ≥ 6.2`, `nvidia-drm.modeset=1` (read `/sys/module/nvidia_drm/parameters/modeset`).
3. Maintain the **X-name ↔ DRM-name mapping** (`DP-0`≠`DP-1`, `HDMI-0`≠`HDMI-A-1`).
4. Warn users: Wayland EDID override disables VRR (bug 4797139); DSC displays may ignore overrides; HDMI > 165 MHz pclk needs VSDB edits, not just a new DTD.
5. Never emit removed tokens (`NoDFPNativeResolutionCheck`, `IgnoreEDID`); use `ModeDebug` for failure diagnostics on X11.
