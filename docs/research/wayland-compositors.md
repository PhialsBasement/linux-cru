# Custom Resolutions & Refresh Rates on Wayland — Technical Report for a Linux CRU Port

*Research date: 2026-08-10. All claims verified against official docs, man pages, source code, or upstream trackers; URLs inline. Version numbers matter a lot here — this landscape changed significantly between late 2025 and early 2026 (notably Plasma 6.6).*

---

## 1. Fundamental architecture

On Wayland there is **no xrandr equivalent that works everywhere**, by design:

- The compositor is the **DRM master**. It alone performs modesetting via the kernel DRM/KMS API (atomic commits on connectors/CRTCs). Clients cannot touch modes.
- The mode list a compositor exposes comes from the **KMS connector's mode list**, which the kernel builds by parsing the monitor's **EDID** (plus a few driver-injected standard modes). Compositors read this list and re-expose it through their own protocol (`wl_output`/`wlr-output-management`/`kde_output_device_v2`) or IPC (Mutter's D-Bus API).
- The Wayland core protocol has **no output-configuration interface at all**. Output configuration is per-compositor: a wlroots protocol extension, a KDE private protocol, a GNOME D-Bus API, or a config file. A CRU-style tool therefore needs **multiple backends**.
- **Can arbitrary modelines be injected at the compositor level?** Yes, but only where the compositor explicitly implements it. The kernel API allows userspace to pass *any* `drmModeModeInfo` (full timings) in an atomic commit — the "EDID mode list" is advisory, not enforced by KMS. Whether you can reach that capability depends entirely on the compositor: wlroots compositors and Weston expose it (modelines and/or CVT-generated custom modes), KWin exposes it since Plasma 6.6, and Mutter does not expose it at all.
- There is a second, orthogonal distinction: **full modeline injection** (you supply pixel clock + all blanking intervals, like X11) vs **`WxH@Hz` custom modes** (compositor computes CVT timings internally). Both exist on Wayland, in different places (see §5).

Sources: [wlr output management protocol](https://wayland.app/protocols/wlr-output-management-unstable-v1), [mutter issue #2856](https://gitlab.gnome.org/GNOME/mutter/-/issues/2856), [sway-output(5)](https://man.archlinux.org/man/sway-output.5).

---

## 2. The protocol layer

### 2.1 wlr-output-management-unstable-v1 (the closest thing to a standard)

- Protocol: [`zwlr_output_manager_v1`](https://wayland.app/protocols/wlr-output-management-unstable-v1), currently **version 4**. Origin: [wlr-protocols PR #38](https://github.com/swaywm/wlr-protocols/pull/38).
- Key requests on `zwlr_output_configuration_head_v1`:
  - `set_mode(mode)` — pick a mode object advertised by the compositor (EDID-derived).
  - **`set_custom_mode(width: int, height: int, refresh: int /* mHz, 0 = unspecified */)`** — ask for a non-advertised mode. Only resolution + refresh; **no timing parameters**. The compositor decides how (or whether) to realize it — "compositors may apply rounding or adjustments."
- **Compositor support (verified from the wayland.app compatibility matrix, parsed directly from the page's HTML table, Aug 2026):**
  - Implements v4: **Sway 1.11, Hyprland 0.52.1, Labwc 0.9.2, river 0.3.13, niri 25.11, COSMIC 1.0.0-beta, Wayfire 0.9, Cage, Jay, Louvre, phoc, Treeland**.
  - Does **NOT** implement it: **Mutter (49.2), KWin (6.6), Weston (14.0.2), Mir, GameScope, Muffin**. (Beware: some summaries/LLM outputs claim Mutter/KWin support it — the actual matrix cell is "x" for both. `wlr-randr` will print "compositor doesn't support wlr-output-management-unstable-v1" on GNOME and KDE.)
- KWin developers explicitly declined to prioritize it: "wlr-output-management-unstable-v1 is missing half the features of the KDE protocol" ([KDE bug 479701](https://www.mail-archive.com/kde-bugs-dist@kde.org/msg874116.html)).

### 2.2 Other protocols

- **`kde-output-management-v2` / `kde-output-device-v2`** — KDE's private protocols; "a desktop environment implementation detail, regular clients must not use this protocol." Used by libkscreen/kscreen-doctor. Plasma 6.6 extended them with a mode-list interface carrying a `reduced_blanking` flag for custom modes. [wayland.app/kde-output-management-v2](https://wayland.app/protocols/kde-output-management-v2), [plasma-wayland-protocols source](https://github.com/KDE/plasma-wayland-protocols/blob/master/src/protocols/kde-output-management-v2.xml).
- **`cosmic-output-management-unstable-v1`** — COSMIC's extension "designed against version 4 of wlr-output-management," mainly adding explicit mirroring. [wayland.app](https://wayland.app/protocols/cosmic-output-management-unstable-v1).
- **GNOME**: no Wayland protocol at all — output config is the **`org.gnome.Mutter.DisplayConfig` D-Bus API** (§3.1).
- **No standardized `ext-output-management`** exists in upstream wayland-protocols as of August 2026. Searches of wayland.app's protocol index and wayland-protocols turn up nothing; wlr-output-management remains "unstable" and de-facto.

---

## 3. Per-compositor capability & command reference

### 3.1 GNOME / Mutter

**Custom (non-EDID) modes: IMPOSSIBLE at the compositor level. Verified.**

- [Mutter issue #2856 "Custom modeline support for Wayland"](https://gitlab.gnome.org/GNOME/mutter/-/issues/2856) is **still open** (feature request, no implementation).
- A GNOME developer response on Discourse regarding xrandr-style mode injection: "something equivalent is neither planned, nor intended for Wayland" ([discourse thread](https://discourse.gnome.org/t/wayland-gdm-add-custom-monitor-resolution/6104)).
- Every configuration channel below can only *select* modes Mutter already got from KMS/EDID. The only way to get a new mode into GNOME Wayland is the **kernel EDID override / `video=` path (§6)** — the modes then appear in KMS and GNOME lists them like any other.

**Mode lists / current state:**
- GUI: gnome-control-center Displays panel — resolution/refresh dropdowns populated strictly from EDID-derived modes; no "add mode" UI.
- D-Bus: `org.gnome.Mutter.DisplayConfig` on `/org/gnome/Mutter/DisplayConfig`:
  - `GetCurrentState()` → serial + monitors, each with a mode array (mode **id string** like `"1920x1080@60.000"`, width, height, refresh, flags incl. `is-current`, `is-preferred`).
  - `ApplyMonitorsConfig(serial, method, logical_monitors, properties)` — `method` 1 = temporary, 2 = persistent. The mode is referenced **by id string from GetCurrentState**; you cannot pass timings. Interface XML: [org.gnome.Mutter.DisplayConfig.xml](https://gitlab.gnome.org/GNOME/mutter/-/blob/main/data/dbus-interfaces/org.gnome.Mutter.DisplayConfig.xml). Usage discussion: [GNOME Discourse](https://discourse.gnome.org/t/how-to-programmatically-change-display-resolution-with-dbus/20436).

**CLI tools (all are DisplayConfig D-Bus clients):**
- **`gdctl`** — official, shipped **with Mutter since GNOME 48** ([Phoronix](https://www.phoronix.com/news/GNOME-Display-Control-gdctl), [man gdctl(1)](https://man.archlinux.org/man/gdctl.1.en.txt)):
  ```
  gdctl show --modes --properties        # list monitors + mode IDs
  gdctl set --logical-monitor --primary --monitor DP-1 --mode 1920x1080@144.000
  gdctl set --persistent ...             # -P writes monitors.xml
  gdctl set --verify ...                 # dry-run
  ```
  `--mode` accepts only advertised mode IDs; no custom-mode option exists.
- **`gnome-monitor-config`** (jadahl, the historical reference client) — deprecated; README says "GNOME 47+ users should use gdctl". Syntax: `gnome-monitor-config set -LpM DP-1 -t normal -m 3840x1600@143.998` ([repo](https://github.com/jadahl/gnome-monitor-config)).
- **`gnome-randr` / gnome-randr-rust** — third-party xrandr-alike over the same D-Bus API (`cargo install gnome-randr`); unmaintained, README itself now recommends gdctl ([repo](https://github.com/maxwellainatchi/gnome-randr-rust)). Same limitation: existing modes only.

**`~/.config/monitors.xml`:**
- Schema documented in [mutter/doc/monitor-configuration.md](https://github.com/GNOME/mutter/blob/main/doc/monitor-configuration.md): root `<monitors version="2">`, per-setup `<configuration>` elements, optional `<policy>` (`<stores>`, `<dbus>`); system-level file at `/etc/xdg/monitors.xml`.
- The `<mode><width/><height/><rate/></mode>` values are **matched against the monitor's actual mode list at session start; hand-writing a non-EDID mode does not create one** — the configuration simply fails validation and is discarded (users who tried report it has no effect; [discourse](https://discourse.gnome.org/t/wayland-gdm-add-custom-monitor-resolution/6104)). It is a *persistence* file, not a mode-injection mechanism.
- GDM/login screen reads its own copy (`/var/lib/gdm/.config/monitors.xml`).

**VRR:** experimental feature GNOME 46–49: `gsettings set org.gnome.mutter experimental-features "['variable-refresh-rate']"` → per-monitor "Refresh Rate → Variable" in Settings. **GNOME 50 (March 2026) promoted VRR out of experimental** — no gsettings flag needed, still opt-in per monitor ([Phoronix](https://www.phoronix.com/news/GNOME-50-VRR-Not-Experimental), [Arch wiki VRR](https://wiki.archlinux.org/title/Variable_refresh_rate)).

### 3.2 KDE Plasma / KWin (Wayland)

**Custom modes: IMPOSSIBLE through Plasma 6.5 — SUPPORTED from Plasma 6.6 (released 2026-02-17).** This is the single biggest recent change for a CRU tool.

- **Plasma ≤ 6.5**: kscreen-doctor/Settings can only select EDID modes; the accepted workaround was kernel-level EDID override ([community gist](https://gist.github.com/mcjmigdal/3079ca80ad6b18bf077dcadc51563fac), [KDE Discuss](https://discuss.kde.org/t/kde-wayland-custom-resolution-and-refresh-rate/13642)).
- **Plasma 6.6**: "You can now use the kscreen-doctor tool to add custom screen modes, useful for supporting exotic or misbehaving screens in the Wayland session" — Xaver Hugl, KDE bug 456697 ([This Week in Plasma, 2025-12-13](https://blogs.kde.org/2025/12/13/this-week-in-plasma-wayland-screen-mirroring-and-custom-modes/), release date [OSTechNix](https://ostechnix.com/kde-plasma-6-6-release-features/)). Implementation: [kwin!8534 "backends/drm: support configuring custom modes"](https://invent.kde.org/plasma/kwin/-/merge_requests/8534) + [libkscreen!266](https://invent.kde.org/plasma/libkscreen/-/merge_requests/266) + plasma-wayland-protocols additions.

**Exact syntax (verified in libkscreen `src/doctor/doctor.cpp` on master):**
```bash
# list outputs, modes, VRR capability
kscreen-doctor -o

# select an existing (EDID) mode — by mode string or index
kscreen-doctor output.HDMI-A-1.mode.1920x1080@60
kscreen-doctor output.1.mode.4

# Plasma 6.6+: add a custom mode
#   output.<name-or-id>.addCustomMode.<width>.<height>.<refresh-in-mHz>[.full|reduced]
kscreen-doctor output.1.addCustomMode.1920.1080.75000.full     # 1920x1080@75, CVT full blanking
kscreen-doctor output.1.addCustomMode.2560.1440.60000.reduced  # CVT reduced blanking

# remove custom mode by index in the custom-mode list
kscreen-doctor output.1.removeCustomMode.0
```
- Refresh is parsed as `toUInt() / 1000.0` → **millihertz**. Last token accepts `reduced` (sets a ReducedBlanking flag) or `full` (default); anything else errors.
- **Timing generation: KWin computes the timings itself with `libxcvt_gen_mode_info(...)` (CVT, with the reduced-blanking bool from the flag)** — same library X.org's `cvt` uses. Verified in the kwin!8534 diff (`DrmConnector::generateMode`). There is **no full-modeline injection** in KWin — width/height/refresh + blanking style only.
- Caveat from the MR: "you can't add a custom mode *and* switch to it atomically" — a CRU tool must `addCustomMode` first, then issue a second `mode.` command.
- The KCM (System Settings → Displays) gained corresponding support in the 6.6 cycle; kscreen-doctor is the scriptable interface.
- Persistence: KWin (Plasma 6) stores applied output config in `~/.config/kwinoutputconfig.json` — kscreen-doctor changes persist through it automatically.

**Other KDE notes:**
- **VRR:** `kscreen-doctor output.<name>.vrrpolicy.<never|always|automatic>` (values verified in doctor.cpp). GUI: Adaptive Sync dropdown in Display settings.
- **Plasma 6.6 also added X11 RandR *emulation* in KWin for XWayland apps** — legacy X apps calling XRandR to switch resolution now get emulated (scaled) resolution changes rather than real modesets ([Phoronix](https://www.phoronix.com/news/Plasma-6.6-KWin-RandR-Emulate)). Don't confuse this with real mode setting; an xrandr call under Plasma Wayland does not add real modes.
- **`KWIN_DRM_*` env vars** ([KWin Environment Variables wiki](https://community.kde.org/KWin/Environment_Variables)): `KWIN_DRM_DEVICES=/dev/dri/card1[:card0]` (GPU selection), `KWIN_DRM_NO_AMS`, `KWIN_DRM_FORCE_LEGACY` etc. — debugging/backend knobs; **none of them inject modes**. No `KWIN_DRM_*` variable exists for custom resolutions.
- KWin does **not** implement wlr-output-management (matrix "x"; [bug 479701](https://www.mail-archive.com/kde-bugs-dist@kde.org/msg874116.html)), so `wlr-randr` does not work on Plasma. Use libkscreen/kscreen-doctor or the (unofficial) `kde-output-management-v2` protocol.

### 3.3 wlroots family

wlroots is where Wayland custom-mode support has always been best. Two paths exist in the library itself:
1. `wlr_output_state_set_custom_mode(w, h, refresh_mHz)` — the DRM backend then **generates a CVT timing via libdisplay-info** (`generate_cvt_mode()` in `backend/drm/util.c`, `DI_CVT_REDUCED_BLANKING_NONE` i.e. **full blanking, not CVT-RB — you cannot choose**). Verified in current master: [util.c](https://gitlab.freedesktop.org/wlroots/wlroots/-/blob/master/backend/drm/util.c), [drm.c](https://gitlab.freedesktop.org/wlroots/wlroots/-/blob/master/backend/drm/drm.c). Historic implementation: [backend/drm/cvt.c](https://github.com/swaywm/wlroots/blob/master/backend/drm/cvt.c).
2. `wlr_drm_connector_add_mode(output, drmModeModeInfo)` — full user-supplied modeline appended to the output's mode list (DRM backend only). This is what compositor `modeline` commands use.

Caveat: custom modes can be rejected by the driver at commit time (e.g. ["wlr_output_commit fails after setting custom mode"](https://github.com/swaywm/wlroots/issues/2572), [sway #5041](https://github.com/swaywm/sway/issues/5041)) — a CRU tool must handle apply-failure gracefully.

#### Sway
Man page: [sway-output(5)](https://man.archlinux.org/man/sway-output.5). Config `~/.config/sway/config`, runtime via `swaymsg`.
```
# select advertised mode
output DP-1 mode 1920x1080@144Hz

# CUSTOM WxH@Hz mode (compositor generates CVT-full-blanking timings)
output DP-1 mode --custom 1920x1200@60Hz

# FULL X11-style modeline (DRM backend only; generate with cvt(1)/gtf(1)) — added in sway 1.7
output HDMI-A-1 modeline 173.00 1920 2048 2248 2576 1080 1083 1088 1120 -hsync +vsync

# VRR
output DP-1 adaptive_sync on|off|toggle

# introspection
swaymsg -t get_outputs
swaymsg 'output DP-1 mode --custom 1920x1200@60Hz'   # runtime apply
```
The man page's own warning on `--custom`: "You should probably only use this if you know what you're doing." Modeline command origin: merged for [sway 1.7](https://github.com/swaywm/sway/releases/tag/1.7) (author David Rosca); source of truth: [sway-output.5.scd](https://github.com/swaywm/sway/blob/master/sway/sway-output.5.scd).

#### wlr-randr (generic CLI for any wlr-output-management compositor)
Man page: [wlr-randr(1)](https://man.archlinux.org/man/extra/wlr-randr/wlr-randr.1.en).
```
wlr-randr                                            # list outputs + modes
wlr-randr --output DP-1 --mode 1920x1080@144.001007Hz
wlr-randr --output DP-1 --custom-mode 1920x1200@60Hz   # -> set_custom_mode -> CVT in compositor
wlr-randr --output DP-1 --adaptive-sync enabled|disabled
```
Works on Sway, Hyprland, Labwc, river, niri, Wayfire, COSMIC, phoc, etc.; **not** on GNOME, KDE, Weston (§2.1). Refresh in the mode string is float Hz; `set_custom_mode` carries it as mHz. `kanshi` (profile-based autoconfig, `~/.config/kanshi/config`) also supports `mode --custom WxH@Hz` ([kanshi PR #78](https://github.com/emersion/kanshi/pull/78)).

#### Hyprland
**Full modeline support exists** (added via [issue #3143](https://github.com/hyprwm/Hyprland/issues/3143)). Config `~/.config/hypr/hyprland.conf`. **Version caveat: the wiki states hyprlang config is deprecated in favor of Lua since Hyprland 0.55**; both syntaxes below verified from the wiki repo/site.

*Classic hyprlang (≤0.54, still what virtually all users run) — [0.54 wiki, Monitors](https://wiki.hypr.land/0.54.0/Configuring/Monitors/):*
```ini
# normal (must correspond to an advertised mode; unlisted modes are not applied —
# see discussion hyprwm/Hyprland#12064)
monitor = DP-1, 1920x1080@144, 0x0, 1

# CUSTOM MODELINE: replace the resolution field with "modeline <clock> <h...> <v...> <flags>"
monitor = DP-1, modeline 1071.101 3840 3848 3880 3920 2160 2263 2271 2277 +hsync -vsync, 0x0, 1

# per-monitor VRR (mode values as misc:vrr)
monitor = DP-1, 1920x1080@144, 0x0, 1, vrr, 1

# monitorv2 block form (0.45+)
monitorv2 {
  output = DP-1
  mode = 1920x1080@144        # or: mode = modeline 1071.101 3840 ... +hsync -vsync
  position = 0x0
  scale = 1
  vrr = 1
}
```
*Lua config (0.55+) — [current wiki source](https://github.com/hyprwm/hyprland-wiki), `content/Configuring/Basics/Monitors.md`:*
```lua
hl.monitor({
  output = "DP-1",
  mode = "modeline 1071.101 3840 3848 3880 3920 2160 2263 2271 2277 +hsync -vsync",
  position = "0x0",
  scale = 1,
  vrr = 1,   -- 0 off, 1 on, 2 fullscreen only, 3 fullscreen w/ video|game content
})
```
Global VRR: `misc:vrr = 0|1|2|3` (verified from [Variables wiki](https://wiki.hypr.land/0.54.0/Configuring/Variables/)). Runtime apply: `hyprctl keyword monitor "DP-1, modeline ..., 0x0, 1"`; list modes with `hyprctl monitors all`. monitorv2 origin: [PR #9761](https://github.com/hyprwm/Hyprland/pull/9761).

#### Labwc
**No mode configuration in its own config files** (`rc.xml` has no output-mode settings — [labwc-config(5)](https://labwc.github.io/labwc-config.5.html)). Labwc implements wlr-output-management; the documented approach is external tools in `~/.config/labwc/autostart`:
```
wlr-randr --output HDMI-A-1 --custom-mode 1920x1200@60Hz
```
or kanshi for hotplug profiles ([labwc integration docs](https://labwc.github.io/integration.html), [Arch wiki](https://wiki.archlinux.org/title/Labwc)). Custom modes work because the wlroots backend handles them.

#### river
**No output configuration in riverctl** — river deliberately delegates output management to wlr-output-management clients. Use `wlr-randr` (users confirm `--custom-mode` works on river) or kanshi, typically launched from the river init script.

### 3.4 COSMIC (System76, cosmic-comp)

- CLI: **`cosmic-randr`** — "utility for displaying and configuring Wayland outputs, using the wlr output management protocols"; also what COSMIC Settings uses ([repo](https://github.com/pop-os/cosmic-randr), announcement by Michael Murphy). Subcommands (verified in `cli/src/main.rs`): `list [--kdl]`, `mode <OUTPUT> <WIDTH> <HEIGHT> [--refresh <Hz>] [--adaptive-sync <mode>]`, `enable`, `disable`, `mirror <output> <from>`, position/scale/transform:
  ```
  cosmic-randr list --kdl
  cosmic-randr mode DP-1 1920 1080 --refresh 60
  ```
- cosmic-comp implements wlr-output-management v4 + its own `cosmic-output-management-unstable-v1` mirroring extension, so plain `wlr-randr` also works.
- **Custom (non-EDID) modes: currently BROKEN/unsupported.** `cosmic-randr mode DP-1 1340 800` and `wlr-randr --custom-mode` both return "configuration failed" — cosmic-comp rejects `set_custom_mode` for modes not in the EDID list. Tracked as **open** bug [pop-os/cosmic-epoch#2577](https://github.com/pop-os/cosmic-epoch/issues/2577) (as of Dec 2025, unassigned). Treat COSMIC like GNOME for now: EDID-override fallback required.

### 3.5 Weston (reference compositor — included because it's the other full-modeline holdout)

`weston.ini` `[output]` accepts a **complete X-style modeline** in `mode=` (DRM backend), generated with `cvt(1)` ([weston.ini(5)](https://man.archlinux.org/man/weston.ini.5), [Arch wiki](https://wiki.archlinux.org/title/Weston)):
```ini
[output]
name=VGA1
mode=173.00 1920 2048 2248 2576 1080 1083 1088 1120 -hsync +vsync
```
Note Weston does *not* implement wlr-output-management (matrix "x"), so this is config-file-only.

### 3.6 niri (bonus)

Implements wlr-output-management v4 (so `wlr-randr --custom-mode` is available); its own config takes `output "DP-1" { mode "1920x1080@60.000"; }` matched against advertised modes.

---

## 4. Summary matrix

| Compositor | Select EDID mode | `WxH@Hz` custom (CVT computed) | Full modeline | Mechanism | Persistent config |
|---|---|---|---|---|---|
| **GNOME/Mutter** (≥48) | `gdctl set --mode`, D-Bus, GUI | **NO** (issue #2856 open; "not planned") | **NO** | `org.gnome.Mutter.DisplayConfig` D-Bus | `~/.config/monitors.xml` (existing modes only) |
| **KDE/KWin ≤6.5** | `kscreen-doctor output.X.mode.WxH@RR` | NO | NO | libkscreen / kde-output-management-v2 | `~/.config/kwinoutputconfig.json` |
| **KDE/KWin 6.6+** (2026-02) | same | **YES** — `output.X.addCustomMode.W.H.mHz[.full\|reduced]` (libxcvt CVT/CVT-RB) | NO | same | same |
| **Sway** | `output X mode WxH@RRHz` | **YES** — `mode --custom` (libdisplay-info CVT, full blanking) | **YES** — `output X modeline ...` (DRM only, sway ≥1.7) | sway config / swaymsg / wlr-output-mgmt | sway config |
| **Hyprland** | `monitor=`/`monitorv2`/Lua `hl.monitor` | via modeline (plain unlisted `WxH@RR` not applied) | **YES** — `mode = modeline <clock> ... ±hsync ±vsync` | hyprland config / hyprctl / wlr-output-mgmt | hyprland.conf / Lua |
| **Labwc / river** | wlr-randr / kanshi | **YES** — `wlr-randr --custom-mode` | NO (no modeline front-end) | wlr-output-management | autostart scripts / kanshi profiles |
| **COSMIC** | `cosmic-randr mode OUT W H --refresh R` | **Broken** (open bug #2577) | NO | wlr-output-mgmt + cosmic ext | COSMIC settings state |
| **Weston** | `weston.ini mode=WxH@RR` | NO | **YES** — `mode=<modeline>` in weston.ini | config file only | weston.ini |

---

## 5. How custom timings are actually generated (is there a "modeline" on Wayland?)

- **wlroots** (`--custom-mode`, `set_custom_mode`): compositor-side **CVT via libdisplay-info**, hard-coded to `DI_CVT_REDUCED_BLANKING_NONE` (full blanking; no RB choice) — [util.c](https://gitlab.freedesktop.org/wlroots/wlroots/-/blob/master/backend/drm/util.c).
- **KWin 6.6**: compositor-side **CVT via libxcvt**, with user-selectable reduced blanking (`.reduced` suffix) — verified in [kwin!8534](https://invent.kde.org/plasma/kwin/-/merge_requests/8534) diff (`libxcvt_gen_mode_info(w, h, rate, reducedBlanking, false)`).
- **Literal X11-style modeline strings survive in exactly four places on the Wayland stack**: Sway's `output modeline`, Hyprland's `modeline` mode field, Weston's `weston.ini mode=`, and the kernel's `drm_mode` parser (EDID override / `video=`). All use the same `clock hdisp hsync_start hsync_end htotal vdisp vsync_start vsync_end vtotal ±hsync ±vsync` layout you already generate for xorg.conf — **the existing modeline generator is directly reusable** for those targets, and `cvt`/`gtf` remain the reference generators.
- KMS itself takes full `drmModeModeInfo` structs; "fixed modes from DRM" vs "computed" is purely a compositor policy choice.

---

## 6. Universal fallback: kernel-level (brief — covered in depth in kernel-drm-edid.md)

Because compositor mode lists come from KMS, injecting modes **below** the compositor works everywhere, including GNOME and COSMIC:
- `video=HDMI-A-1:1920x1080@60` kernel parameter (kernel computes CVT/GTF; `e`/`D` suffixes force enable/digital), and
- **EDID override**: `drm.edid_firmware=HDMI-A-1:edid/custom.bin` with a patched EDID binary in `/usr/lib/firmware/edid/` (verify via `dmesg | grep -i edid`, `/sys/class/drm/card0-HDMI-A-1/edid`). This is the documented KDE-pre-6.6/GNOME workaround ([gist](https://gist.github.com/mcjmigdal/3079ca80ad6b18bf077dcadc51563fac), [NVIDIA forum thread](https://forums.developer.nvidia.com/t/custom-edid-in-wayland/302923)). A CRU port arguably wants this as its "works everywhere" backend, with compositor-native paths as the no-reboot fast path.

---

## 7. VRR / adaptive-sync quick reference

| Compositor | Toggle |
|---|---|
| GNOME 46–49 | `gsettings set org.gnome.mutter experimental-features "['variable-refresh-rate']"` then Displays → Refresh Rate → Variable; **GNOME 50: non-experimental**, setting always visible |
| KDE Plasma | `kscreen-doctor output.<name>.vrrpolicy.never\|always\|automatic`; GUI Adaptive Sync dropdown |
| Sway | `output <name> adaptive_sync on\|off\|toggle` |
| Hyprland | `misc:vrr = 0\|1\|2\|3` global; per-monitor `vrr` field / `,vrr,X` |
| wlr-randr (labwc/river/etc.) | `wlr-randr --output X --adaptive-sync enabled\|disabled` |
| COSMIC | `cosmic-randr mode ... --adaptive-sync <mode>` |

---

## 8. Practical implications for the CRU port

1. **Backend detection**: key off `XDG_CURRENT_DESKTOP`/`WAYLAND_DISPLAY` + probe: bind `zwlr_output_manager_v1` (covers the whole wlroots family + COSMIC + niri with one client implementation, including `set_custom_mode`); D-Bus `org.gnome.Mutter.DisplayConfig` for GNOME (mode *selection* only); shell out to `kscreen-doctor` (or link libkscreen) for KDE, gating `addCustomMode` on Plasma ≥ 6.6.
2. **Custom-mode writers**: reuse the modeline generator for Sway (`output modeline`), Hyprland (`modeline` in conf — mind the hyprlang→Lua transition at 0.55), Weston (`weston.ini`), and kernel EDID/`video=` fallback. For KWin 6.6 and wlroots `set_custom_mode`, emit `W/H/refresh(+blanking flag)` instead — the compositor computes CVT.
3. **Hard walls to surface in the UI**: GNOME = no compositor-level custom modes, period (offer the EDID-override flow); COSMIC = custom modes currently rejected (open bug); KDE < 6.6 = same, suggest upgrade or EDID override; KWin 6.6 add-then-apply must be two steps.

Sources not linked inline above: [sway releases](https://github.com/swaywm/sway/releases), [Plasma 6.6 display improvements](https://linuxiac.com/kde-plasma-6-6-will-enhance-display-handling-and-scaling-on-wayland/), [kscreen-doctor overview](https://linuxcommandlibrary.com/man/kscreen-doctor), [Manjaro forum: custom VGA resolution on Wayland](https://forum.manjaro.org/t/how-can-i-make-a-custom-resolution-for-vga-monitor-in-wayland/174597), [Hyprland mode-not-listed discussion](https://github.com/hyprwm/Hyprland/discussions/12064), [mutter VRR MR !1154](https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/1154).
