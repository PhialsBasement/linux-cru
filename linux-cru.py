#!/usr/bin/env python3
"""Linux Custom Resolution Utility.

GUI front-end over linux_cru: environment detection, VESA timing
calculation, live testing via xrandr (X11) with auto-revert, and
persistence via /etc/X11/xorg.conf.d. On Wayland, generates the
compositor-native commands where they exist (see docs/RESEARCH.md);
the kernel EDID-override backend is the next milestone.
"""

import os
import subprocess
import sys
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tempfile

from linux_cru import (detect, edid, hostenv, override, persist, privileged,
                       stretch, timings, wayland)

TEST_REVERT_SECONDS = 15


class PasswordPrompt:
    """Modal password dialog, used when no polkit agent is available."""

    def __init__(self, parent, message, retry=False):
        self.password = None
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Administrator access required")
        self.dialog.transient(parent)
        self.dialog.resizable(False, False)
        self.dialog.grab_set()

        body = ttk.Frame(self.dialog, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(body, text=message, wraplength=360,
                  justify="left").pack(anchor="w")
        if retry:
            ttk.Label(body, text="Incorrect password, try again.",
                      foreground="#b00000").pack(anchor="w", pady=(6, 0))

        ttk.Label(body, text="Password:").pack(anchor="w", pady=(10, 2))
        self.entry = ttk.Entry(body, show="•", width=32)
        self.entry.pack(fill=tk.X)

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(buttons, text="Cancel",
                   command=self.cancel).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="OK",
                   command=self.accept).pack(side=tk.RIGHT, padx=(0, 6))

        self.dialog.bind("<Return>", lambda e: self.accept())
        self.dialog.bind("<Escape>", lambda e: self.cancel())
        self.dialog.protocol("WM_DELETE_WINDOW", self.cancel)

        parent.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - 180
        y = parent.winfo_rooty() + (parent.winfo_height() // 3)
        self.dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.entry.focus_set()

    def accept(self):
        self.password = self.entry.get()
        self.dialog.destroy()

    def cancel(self):
        self.password = None
        self.dialog.destroy()

    def run(self):
        self.dialog.wait_window()
        return self.password


class LinuxCRU:
    def __init__(self, root):
        self.root = root
        self.root.title("Linux Custom Resolution Utility")
        self.root.geometry("860x980")
        self.root.minsize(640, 720)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        self.env = detect.detect()

        self.main_frame = ttk.Frame(root, padding="10")
        self.main_frame.grid(row=0, column=0, sticky="nsew")
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(5, weight=1)  # preview grows

        self.create_environment_section()
        self.create_display_section()
        self.create_resolution_section()
        self.create_timing_section()
        self.create_stretch_section()
        self.create_preview_section()
        self.create_action_section()

        self.load_current_settings()
        self.generate_preview()
        self.bind_validators()

    # -- environment ---------------------------------------------------------

    def create_environment_section(self):
        env = self.env
        frame = ttk.LabelFrame(self.main_frame, text="Environment", padding=5)
        frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        frame.grid_columnconfigure(0, weight=1)

        session = env.session_type.upper() if env.session_type != "unknown" else "unknown"
        comp = env.compositor
        if env.compositor_version:
            comp += " " + ".".join(str(x) for x in env.compositor_version[:2])
        drivers = ", ".join(env.drivers) or "unknown"
        summary = (f"Session: {session}   Compositor: {comp}   "
                   f"GPU: {drivers}   Kernel: {env.kernel_release}")
        ttk.Label(frame, text=summary).grid(row=0, column=0, sticky="w", padx=5)

        paths = detect.describe_paths(env)
        lbl = ttk.Label(frame, text=paths, wraplength=780, foreground="#555555")
        lbl.grid(row=1, column=0, sticky="w", padx=5, pady=(3, 0))
        self._paths_label = lbl

    # -- display selection ----------------------------------------------------

    def create_display_section(self):
        frame = ttk.LabelFrame(self.main_frame, text="Display Selection", padding=5)
        frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        frame.grid_columnconfigure(0, weight=1)

        self.displays = self.get_displays()
        self.display_var = tk.StringVar(value=self.displays[0] if self.displays else "")
        combo = ttk.Combobox(frame, textvariable=self.display_var,
                             values=self.displays, state="readonly")
        combo.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        combo.bind('<<ComboboxSelected>>', lambda e: self.load_current_settings())

        ttk.Button(frame, text="Get Current Settings",
                   command=lambda: self.load_current_settings(quiet=False)
                   ).grid(row=0, column=1, padx=5, pady=5)

        self.monitor_var = tk.StringVar()
        ttk.Label(frame, textvariable=self.monitor_var, wraplength=780,
                  foreground="#555555").grid(row=1, column=0, columnspan=2,
                                             sticky="w", padx=5, pady=(0, 3))

    def get_displays(self):
        """Output names: xrandr names on X11, DRM connector names on Wayland."""
        if self.env.session_type == "x11":
            names = []
            try:
                out = subprocess.check_output(['xrandr', '-q'], universal_newlines=True,
                                              stderr=subprocess.DEVNULL,
                                              env=hostenv.subprocess_env())
                for line in out.splitlines():
                    if ' connected' in line:
                        names.append(line.split()[0])
            except (OSError, subprocess.SubprocessError):
                pass
            if names:
                return names
        connected = [c.name for c in self.env.connected_connectors()]
        others = [c.name for c in self.env.connectors if c.status != "connected"]
        return connected + others if (connected or others) else ["DP-1"]

    def describe_monitor(self):
        """Identity and declared limits of the selected display."""
        try:
            info = edid.Edid.from_connector(
                self._drm_connector_for(self.display_var.get())).info()
        except edid.EdidError:
            self._edid_info = None
            self.monitor_var.set("No EDID available for this output.")
            return
        self._edid_info = info
        self.monitor_var.set(info.summary())

    def load_current_settings(self, quiet=True):
        """Fill the inputs with the selected display's active mode."""
        self.describe_monitor()
        out = self.display_var.get()
        mode = detect.current_mode(self.env, out)
        if mode:
            w, h, r = mode
            self.width_var.set(str(w))
            self.height_var.set(str(h))
            self.refresh_var.set(f"{round(r, 3):g}")
            self.status_var.set(f"Loaded current mode of {out}: {w}x{h} at {round(r, 3):g} Hz")
        else:
            self.generate_preview()
            if not quiet:
                messagebox.showerror(
                    "Error", f"Could not read the current mode of {out}.")

    # -- resolution inputs ------------------------------------------------------

    def create_resolution_section(self):
        frame = ttk.LabelFrame(self.main_frame, text="Resolution Settings", padding=5)
        frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        self.width_var = tk.StringVar(value="1920")
        self.height_var = tk.StringVar(value="1080")
        self.refresh_var = tk.StringVar(value="75")

        for row, (label, var, unit) in enumerate([
                ("Width:", self.width_var, "pixels"),
                ("Height:", self.height_var, "pixels"),
                ("Refresh Rate:", self.refresh_var, "Hz")]):
            ttk.Label(frame, text=label).grid(row=row, column=0, padx=5, pady=4, sticky="e")
            ttk.Entry(frame, textvariable=var, width=10).grid(row=row, column=1,
                                                              sticky="w", padx=5)
            ttk.Label(frame, text=unit).grid(row=row, column=2, sticky="w", padx=5)

    def bind_validators(self):
        for var in (self.width_var, self.height_var, self.refresh_var):
            var.trace_add("write", lambda *a: self.generate_preview())

    def read_inputs(self):
        """Returns (width, height, refresh) or raises ValueError."""
        w = int(self.width_var.get().strip())
        h = int(self.height_var.get().strip())
        r = float(self.refresh_var.get().strip())
        if w <= 0 or h <= 0 or r <= 0:
            raise ValueError("width/height/refresh must be positive")
        return w, h, r

    # -- timing options ----------------------------------------------------------

    def create_timing_section(self):
        frame = ttk.LabelFrame(self.main_frame, text="Timing Standard", padding=5)
        frame.grid(row=3, column=0, sticky="ew", pady=(0, 10))

        self.standard_var = tk.StringVar(value="cvt-rb2")
        options = [
            ("CVT-RBv2 (recommended for modern displays)", "cvt-rb2"),
            ("CVT-RB (reduced blanking v1)", "cvt-rb"),
            ("CVT (full blanking)", "cvt"),
            ("GTF (for CRT monitors)", "gtf"),
        ]
        for i, (label, value) in enumerate(options):
            ttk.Radiobutton(frame, text=label, variable=self.standard_var, value=value,
                            command=self.generate_preview).grid(row=i, column=0,
                                                                sticky="w", padx=5)

        row = len(options)
        self.relax_validation = tk.BooleanVar(value=False)
        if self.env.has_nvidia_proprietary and self.env.session_type == "x11":
            ttk.Checkbutton(
                frame,
                text="Skip NVIDIA EDID and pixel clock checks (needed for overclocking)",
                variable=self.relax_validation,
                command=self.generate_preview).grid(row=row, column=0,
                                                    sticky="w", padx=5, pady=(6, 0))
            row += 1

        self.boot_method_var = tk.BooleanVar(value=False)
        self.boot_method_check = ttk.Checkbutton(
            frame,
            text="Apply from the kernel command line instead of a boot service "
                 "(needed only for the boot console and login screen; changes "
                 "your boot configuration)",
            variable=self.boot_method_var,
            command=self.generate_preview)
        self.boot_method_check.grid(row=row, column=0, sticky="w",
                                    padx=5, pady=(6, 0))

    # -- stretch (KWin) ------------------------------------------------------------

    def create_stretch_section(self):
        """Scaling behaviour for a resolution smaller than the panel.

        Only built on KWin, which is the compositor that refuses external
        GPU scaling by default and the one this can override.
        """
        self.stretch_mode_var = tk.StringVar(value="off")
        if self.env.compositor != "kwin":
            return

        frame = ttk.LabelFrame(self.main_frame,
                               text="Stretch to fill the screen (KWin)", padding=5)
        frame.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        frame.grid_columnconfigure(0, weight=1)

        options = [
            ("Don't force scaling (KWin only scales on laptop panels)", "off"),
            ("Stretch to fill (distorts aspect; for stretched-res gaming)", "full"),
            ("Fill keeping aspect (scales up, may add black bars)", "full_aspect"),
        ]
        for i, (label, value) in enumerate(options):
            ttk.Radiobutton(frame, text=label, variable=self.stretch_mode_var,
                            value=value).grid(row=i, column=0, sticky="w", padx=5)

        btn_row = ttk.Frame(frame)
        btn_row.grid(row=len(options), column=0, sticky="w", padx=5, pady=(6, 0))
        ttk.Button(btn_row, text="Apply",
                   command=self.apply_stretch).pack(side=tk.LEFT)
        # Only meaningful once something is saved; shown/hidden by refresh.
        self.stretch_revert_btn = ttk.Button(btn_row, text="Revert now",
                                             command=self.revert_stretch)

        self.stretch_status_var = tk.StringVar()
        ttk.Label(frame, textvariable=self.stretch_status_var, wraplength=760,
                  foreground="#555555").grid(row=len(options) + 1, column=0,
                                             sticky="w", padx=5, pady=(4, 0))
        self.refresh_stretch_status()

    def refresh_stretch_status(self):
        if self.env.compositor != "kwin":
            return
        saved = stretch.persistent_value()
        if saved:
            self.stretch_revert_btn.pack(side=tk.LEFT, padx=(6, 0))
            self.stretch_status_var.set(
                f"Saved across reboots: {saved}. Apply takes effect now; "
                "the saved setting also applies at every login. Revert now "
                "turns it off and removes the saved setting.")
        else:
            self.stretch_revert_btn.pack_forget()
            self.stretch_status_var.set(
                "Apply takes effect immediately (needs root); it is lost when "
                "you log out unless you keep it.")

    def revert_stretch(self):
        result = self._run_root_script(stretch.build_live_patch_script("none"),
                                       "stretch-live.py",
                                       interpreter="/usr/bin/python3")
        if result.cancelled:
            self.status_var.set("Cancelled.")
            return
        stretch.remove_persistent()
        self.stretch_mode_var.set("off")
        self.refresh_stretch_status()
        self.status_var.set("Stretch turned off and the saved setting removed."
                            if result.ok else
                            "Saved setting removed; could not change the live "
                            "session: " + result.message)

    def apply_stretch(self):
        value = self.stretch_mode_var.get()          # off / full / full_aspect

        # Apply to the running session first (live poke, no restart).
        script = stretch.build_live_patch_script(
            "none" if value == "off" else value)
        result = self._run_root_script(script, "stretch-live.py",
                                       interpreter="/usr/bin/python3")
        if result.cancelled:
            self.status_var.set("Cancelled.")
            return
        if not result.ok:
            messagebox.showerror(
                "Error",
                f"Could not apply the live stretch:\n{result.message}\n\n"
                "This pokes the running KWin's memory and needs root. If KWin "
                "was updated the signature may need refreshing.")
            return

        if value == "off":
            stretch.remove_persistent()
            self.refresh_stretch_status()
            self.status_var.set("Stretch turned off.")
            return

        self.status_var.set("Stretch is active now. Set a smaller resolution "
                            "and it will fill the screen.")

        # Offer to keep it. Without this it is gone at the next logout.
        keep = messagebox.askyesno(
            "Keep the setting?",
            "Keep this scaling setting as a systemd service?\n\n"
            "It will be lost when you log out if you don't. Keeping it makes "
            "it apply at every login from now on.")
        if keep:
            stretch.write_persistent(value.upper())
            self.status_var.set("Stretch active now and kept for future logins.")
        else:
            stretch.remove_persistent()
        self.refresh_stretch_status()

    # -- preview -------------------------------------------------------------------

    def create_preview_section(self):
        frame = ttk.LabelFrame(self.main_frame, text="Configuration Preview", padding=5)
        frame.grid(row=5, column=0, sticky="nsew", pady=(0, 10))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        self.preview_text = tk.Text(frame, height=12, width=80, wrap=tk.NONE)
        self.preview_text.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        y = ttk.Scrollbar(frame, orient="vertical", command=self.preview_text.yview)
        y.grid(row=0, column=1, sticky="ns")
        x = ttk.Scrollbar(frame, orient="horizontal", command=self.preview_text.xview)
        x.grid(row=1, column=0, sticky="ew")
        self.preview_text.configure(yscrollcommand=y.set, xscrollcommand=x.set)

    def current_modeline(self):
        w, h, r = self.read_inputs()
        ml = timings.calc(w, h, r, self.standard_var.get())
        name = f"{w}x{h}_{r:g}"
        return name, ml

    def generate_preview(self):
        try:
            name, ml = self.current_modeline()
        except ValueError:
            return  # incomplete input while typing
        out = self.display_var.get()

        header = (f"# {name}: {ml.clock_mhz:.3f} MHz pixel clock, "
                  f"{ml.actual_refresh:.3f} Hz actual "
                  f"({self.standard_var.get().upper()})\n"
                  f"# {ml.xorg_modeline(name)}\n\n")
        warning = self.limits_warning(ml)
        if warning:
            header += warning + "\n"

        if self.env.session_type == "x11":
            body = self.build_x11_preview(out, name, ml)
        elif self.env.session_type == "wayland":
            body = self.build_wayland_preview(out, name, ml)
        else:
            body = "Could not detect the session type. Showing the modeline only.\n"

        self.preview_text.delete(1.0, tk.END)
        self.preview_text.insert(1.0, header + body)
        if hasattr(self, "boot_method_check"):
            self.boot_method_check.state(
                ["!disabled"] if self.use_edid_method() else ["disabled"])

        if self.env.session_type == "x11":
            status = ("Method: xrandr and xorg.conf. Use Test Mode to try it, "
                      "then Apply Configuration to save it.")
        elif self.use_edid_method():
            status = ("Method: EDID override (needs root). "
                      "Use Test Mode to try it.")
        else:
            status = (f"Method: {self.env.compositor} (no root needed). "
                      "Use Test Mode to try it.")
        self.status_var.set(status)

    def limits_warning(self, ml):
        """Note anything the display says it cannot do. Not a refusal:
        monitors routinely run past what they declare, which is the whole
        point of this tool, but it explains a mode that gets rejected."""
        info = getattr(self, "_edid_info", None)
        if not info:
            return ""
        notes = []
        if info.max_pixel_clock_mhz and ml.clock_mhz > info.max_pixel_clock_mhz:
            notes.append(f"pixel clock {ml.clock_mhz:.0f} MHz is above the "
                         f"{info.max_pixel_clock_mhz} MHz this display declares")
        if info.max_vrefresh and ml.actual_refresh > info.max_vrefresh + 0.5:
            notes.append(f"{ml.actual_refresh:.0f} Hz is above its declared "
                         f"maximum of {info.max_vrefresh} Hz")
        hsync_khz = ml.clock_khz / ml.htotal
        if info.max_hsync_khz and hsync_khz > info.max_hsync_khz + 0.5:
            notes.append(f"horizontal frequency {hsync_khz:.0f} kHz is above "
                         f"its declared maximum of {info.max_hsync_khz} kHz")
        if not notes:
            return ""
        lines = ["# This mode goes beyond what the display reports:"]
        lines += [f"#   - {n}" for n in notes]
        lines.append("# It may still work. If the driver refuses it, an EDID "
                     "override raises")
        lines.append("# these declared limits, which is what the checks are "
                     "made against.")
        return "\n".join(lines) + "\n"

    def build_x11_preview(self, out, name, ml):
        parts = ["# Test commands (the Test Mode button runs these):\n",
                 f"xrandr --newmode \"{name}\" {ml.timing_string()}\n",
                 f"xrandr --addmode {out} \"{name}\"\n",
                 f"xrandr --output {out} --mode \"{name}\"\n\n",
                 "# Configuration file (Apply Configuration writes this to "
                 "/etc/X11/xorg.conf.d/10-linux-cru.conf):\n\n",
                 self.build_xorg_config(out, name, ml)]
        return "".join(parts)

    def build_xorg_config(self, out, name, ml):
        env = self.env
        conf = [f"# Generated by Linux CRU on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
                'Section "Monitor"\n',
                f'    Identifier "{out}"\n',
                f'    {ml.xorg_modeline(name)}\n',
                f'    Option "PreferredMode" "{name}"\n',
                'EndSection\n']
        if env.has_nvidia_proprietary:
            flags = ["AllowNonEdidModes"]
            if self.relax_validation.get():
                flags += ["NoEdidMaxPClkCheck", "NoMaxPClkCheck",
                          "NoHorizSyncCheck", "NoVertRefreshCheck"]
            conf += ['\nSection "Device"\n',
                     '    Identifier "LinuxCRU-nvidia"\n',
                     '    Driver "nvidia"\n',
                     f'    Option "Monitor-{out}" "{out}"\n',
                     f'    Option "ModeValidation" "{", ".join(flags)}"\n',
                     '    Option "ModeDebug" "true"\n',
                     'EndSection\n']
        return "".join(conf)

    def build_wayland_preview(self, out, name, ml):
        env = self.env
        w, h, r = self.read_inputs()

        if self.use_edid_method():
            reason = detect.why_edid_needed(env, self.standard_var.get())
            return self._edid_override_notes(
                out, name, ml,
                reason=f"Using an EDID override because {reason}.")

        if env.compositor == "sway":
            return ("# Run now:\n"
                    f"swaymsg 'output {out} modeline {ml.timing_string()}'\n\n"
                    "# To make it permanent, add to ~/.config/sway/config:\n"
                    f"output {out} modeline {ml.timing_string()}\n")

        if env.compositor == "hyprland":
            return ("# Run now:\n"
                    f"hyprctl keyword monitor \"{out}, modeline {ml.timing_string()}, 0x0, 1\"\n\n"
                    "# To make it permanent, add to ~/.config/hypr/hyprland.conf:\n"
                    f"monitor = {out}, modeline {ml.timing_string()}, 0x0, 1\n")

        if env.compositor == "kwin":
            mhz = int(round(r * 1000))
            blanking = "full" if self.standard_var.get() == "cvt" else "reduced"
            return ("# KWin takes resolution, refresh rate and a blanking\n"
                    "# choice, then computes the timings itself. They come out\n"
                    "# the same as the ones above, because both use libxcvt.\n"
                    "# Adding a mode and switching to it are separate steps:\n"
                    f"kscreen-doctor output.{out}.addCustomMode.{w}.{h}.{mhz}.{blanking}\n"
                    f"kscreen-doctor output.{out}.mode.{w}x{h}@{r:g}\n\n"
                    "# Test Mode runs both for you and reverts if you do not keep it.\n")

        if env.is_wlroots_family:
            return ("# Run now:\n"
                    f"wlr-randr --output {out} --custom-mode {w}x{h}@{r:g}Hz\n\n"
                    "# The compositor computes CVT full-blanking timings, which\n"
                    "# match the ones above.\n")

        return self._edid_override_notes(
            out, name, ml,
            reason=f"Using an EDID override because "
                   f"{detect.why_edid_needed(env, self.standard_var.get())}.")

    def _edid_override_notes(self, out, name, ml, reason):
        conn = self._drm_connector_for(out)
        short = self._strip_card(conn)
        fw = persist.firmware_path(short)
        minor = self._drm_minor(conn)

        try:
            e = edid.Edid.from_connector(conn)
            placement = "detailed timing descriptor" \
                if e.fits_dtd(ml) else "DisplayID timing record"
        except edid.EdidError:
            placement = "detailed timing / DisplayID record"

        method = (persist.METHOD_CMDLINE if self.boot_method_var.get()
                  else persist.METHOD_SYSTEMD)
        installed = persist.installed_connectors()

        parts = [
            f"# {reason}",
            f"# The {name} timing above is written into a copy of {short}'s EDID",
            f"# as a {placement}, installed at {fw}.",
            "",
            "# --- Test Mode runs, as root (no reboot, auto-reverts) ---",
            f"cat {os.path.basename(fw)} > /sys/kernel/debug/dri/{minor}/{short}/edid_override",
            f"echo 1 > /sys/kernel/debug/dri/{minor}/{short}/trigger_hotplug",
            "",
        ]

        if method == persist.METHOD_SYSTEMD:
            parts += [
                "# --- Apply Configuration installs this boot service ---",
                f"# {persist.SYSTEMD_UNIT}",
                persist.systemd_unit_text().rstrip(),
                "",
                f"# it runs this helper ({persist.SYSTEMD_HELPER}):",
                persist.systemd_helper_text().rstrip(),
            ]
        else:
            param = f"drm.edid_firmware={short}:{persist.firmware_rel(short)}"
            boot, detail = persist.detect_bootloader()
            initrd, _ = persist.detect_initramfs()
            parts += [
                "# --- Apply Configuration adds this to the kernel command line ---",
                f"#   bootloader: {boot} ({detail})",
                param,
                f"# and adds {os.path.basename(fw)} to the initramfs ({initrd}).",
            ]

        parts += [
            "",
            f"# Currently installed overrides: "
            + (", ".join(installed) if installed else "none"),
        ]
        return "\n".join(parts) + "\n"

    def _drm_minor(self, conn):
        card = conn.partition("-")[0]
        try:
            with open(f"/sys/class/drm/{card}/dev") as f:
                return f.read().strip().split(":")[1]
        except OSError:
            return "N"

    def _drm_connector_for(self, out):
        for c in self.env.connectors:
            if c.name == out:
                return f"{c.card}-{c.name}"
        return f"cardX-{out}"

    @staticmethod
    def _strip_card(conn):
        return conn.partition("-")[2] if conn.startswith("card") else conn

    # -- actions --------------------------------------------------------------------

    def create_action_section(self):
        frame = ttk.Frame(self.main_frame)
        frame.grid(row=6, column=0, sticky="ew", pady=(0, 5))
        frame.grid_columnconfigure(1, weight=1)

        ttk.Button(frame, text="Generate Preview",
                   command=self.generate_preview).grid(row=0, column=0, padx=5)

        self.test_btn = ttk.Button(frame, text=f"Test Mode ({TEST_REVERT_SECONDS}s auto-revert)",
                                   command=self.test_mode)
        self.test_btn.grid(row=0, column=2, padx=5)

        self.apply_btn = ttk.Button(frame, text="Apply Configuration",
                                    command=self.apply_configuration)
        self.apply_btn.grid(row=0, column=3, padx=5)

        self.remove_btn = ttk.Button(frame, text="Remove",
                                     command=self.remove_persistent_override)
        self.remove_btn.grid(row=0, column=4, padx=5)
        self.refresh_remove_button()

        self.status_var = tk.StringVar()
        ttk.Label(self.main_frame, textvariable=self.status_var,
                  wraplength=780).grid(row=7, column=0, sticky="ew", pady=5)

    def refresh_remove_button(self):
        if persist.installed_connectors():
            self.remove_btn.state(["!disabled"])
        else:
            self.remove_btn.state(["disabled"])

    # -- method selection ----------------------------------------------------------

    def use_edid_method(self):
        """True when the timings can only be delivered through the EDID."""
        if self.env.session_type == "x11":
            return False
        return not detect.compositor_can_apply(self.env, self.standard_var.get())

    # -- EDID override backend ------------------------------------------------------

    def _build_patched_edid(self, out, ml):
        """(patched_bytes, placement) for the selected display, or raises."""
        conn = self._drm_connector_for(out)
        e = edid.Edid.from_connector(conn)
        placement = e.add_mode(ml)
        return e.to_bytes(), placement

    def _ask_password(self, message, retry):
        return PasswordPrompt(self.root, message, retry).run()

    def _run_root_script(self, script, name, interpreter="/bin/bash"):
        """Write `script` to the work dir and run it as root."""
        path = os.path.join(self._work_dir(), name)
        with open(path, "w") as f:
            f.write(script)
        os.chmod(path, 0o755)
        return privileged.run_as_root([interpreter, path],
                                      ask_password=self._ask_password)

    def _work_dir(self):
        if not getattr(self, "_workdir_path", None):
            self._workdir_path = tempfile.mkdtemp(prefix="linux-cru-")
            os.chmod(self._workdir_path, 0o755)
        return self._workdir_path

    def _test_mode_edid(self, out, ml):
        conn = self._drm_connector_for(out)
        card = conn.partition("-")[0]
        connector = self._strip_card(conn)
        w, h, r = self.read_inputs()

        try:
            patched, placement = self._build_patched_edid(out, ml)
        except edid.EdidError as e:
            messagebox.showerror("Error", f"Could not build the EDID:\n{e}")
            return

        edid_path = os.path.join(self._work_dir(), f"{connector}.bin")
        with open(edid_path, "wb") as f:
            f.write(patched)
        os.chmod(edid_path, 0o644)

        previous = detect.current_mode(self.env, out)

        script = override.build_test_script(card, connector, edid_path,
                                            TEST_REVERT_SECONDS + 20)
        result = self._run_root_script(script, "test-override.sh")
        if result.cancelled:
            self.status_var.set("Cancelled.")
            return
        if not result.ok:
            messagebox.showerror(
                "Error",
                f"Could not apply the EDID override:\n{result.message}\n\n"
                "This needs root access, and debugfs must not be restricted "
                "by kernel lockdown.")
            return

        def remove_override():
            self._run_root_script(override.build_revert_script(card, connector),
                                  "revert-override.sh")

        # The mode only exists once the kernel has re-read the EDID.
        if not wayland.wait_for_mode(card, connector, w, h):
            remove_override()
            messagebox.showerror(
                "Error",
                f"The kernel did not pick up {w}x{h} after the EDID was "
                "applied.\n\nThe driver most likely rejected it: the pixel "
                "clock may be beyond what the connection can carry. Check "
                "dmesg.")
            return

        ok, message = wayland.set_mode(self.env, out, w, h, r)
        if not ok:
            # The mode is there, we just cannot select it from here.
            self.status_var.set(f"Mode added to {out}, but it could not be "
                                f"selected automatically.")
            keep = messagebox.askyesno(
                "Added, but not applied",
                f"{w}x{h} at {r:g} Hz was added to {out}, but this tool could "
                f"not switch to it:\n\n{message}\n\nKeep the mode available "
                f"so you can select it yourself?")
            if keep:
                self._run_root_script(
                    f"#!/bin/bash\ntouch '{override.keep_flag_path(connector)}'\n",
                    "keep-override.sh")
                self.status_var.set("Mode kept for this session.")
            else:
                remove_override()
                self.status_var.set("Reverted.")
            return

        def revert():
            # Order matters: leave the custom mode before taking away the
            # EDID that defines it, or the output is left on a mode that
            # no longer exists.
            if previous:
                pw, ph, pr = previous
                wayland.set_mode(self.env, out, pw, ph, pr)
                time.sleep(1.0)
            remove_override()

        def keep():
            self._run_root_script(
                f"#!/bin/bash\ntouch '{override.keep_flag_path(connector)}'\n",
                "keep-override.sh")

        self.status_var.set(f"Testing {w}x{h} at {r:g} Hz on {out}.")
        self._show_revert_dialog(
            out, revert,
            keep_status="Mode kept for this session. Use Apply Configuration "
                        "to keep it after a reboot.",
            on_keep=keep,
            message=f"{out} is now running at {w}x{h}, {r:g} Hz\n"
                    f"(added to the EDID as {placement}).")

    def _apply_edid_override(self, out, ml):
        conn = self._drm_connector_for(out)
        connector = self._strip_card(conn)
        try:
            patched, placement = self._build_patched_edid(out, ml)
        except edid.EdidError as e:
            messagebox.showerror("Error", f"Could not build the EDID:\n{e}")
            return

        method = (persist.METHOD_CMDLINE if self.boot_method_var.get()
                  else persist.METHOD_SYSTEMD)
        steps = "\n".join(f"  {i}. {s}" for i, s in
                          enumerate(persist.describe_plan(connector, method), 1))

        if method == persist.METHOD_CMDLINE:
            warning = ("\nThis changes your boot configuration. If the display "
                       "ever fails to come up, remove the drm.edid_firmware "
                       "parameter from the kernel command line in your "
                       "bootloader menu.\n")
        else:
            warning = ("\nYour boot configuration is not touched, so this "
                       "cannot stop the machine from booting.\n")

        if not messagebox.askyesno(
                "Make permanent",
                "This keeps the mode in the display's list at every boot. It "
                "does not change your resolution -- you still select the mode "
                f"yourself in your display settings.\n\n{steps}\n{warning}\n"
                "Continue?"):
            return

        edid_path = os.path.join(self._work_dir(), f"{connector}-persist.bin")
        with open(edid_path, "wb") as f:
            f.write(patched)
        os.chmod(edid_path, 0o644)

        result = self._run_root_script(
            persist.build_install_script(connector, edid_path, method),
            "install.sh")
        self.refresh_remove_button()
        if result.cancelled:
            self.status_var.set("Cancelled.")
            return
        if result.ok:
            if method == persist.METHOD_CMDLINE:
                when = ("After a reboot the mode is in the display's list. "
                        "Select it in your display settings.")
            else:
                when = ("The mode is in the display's list now and is re-added "
                        "at every boot. Select it in your display settings -- "
                        "your resolution is not changed automatically.")
            messagebox.showinfo(
                "Installed",
                f"The mode was added to {out}'s EDID as a {placement}.\n\n{when}\n\n"
                "Use Remove to undo it.")
            self.status_var.set("Custom mode installed and kept across reboots.")
        else:
            messagebox.showerror("Error", f"Installation failed:\n{result.message}")

    def remove_persistent_override(self):
        installed = persist.installed_connectors()
        if not installed:
            return
        out = self._strip_card(self._drm_connector_for(self.display_var.get()))
        target = out if out in installed else None
        what = f"the override for {target}" if target else \
               f"all overrides ({', '.join(installed)})"
        if not messagebox.askyesno("Remove", f"Remove {what}?"):
            return
        result = self._run_root_script(
            persist.build_uninstall_script(target), "uninstall.sh")
        self.refresh_remove_button()
        if result.cancelled:
            self.status_var.set("Cancelled.")
            return
        if result.ok:
            messagebox.showinfo(
                "Removed",
                "Removed. The mode stays in the list until you reboot or "
                "replug the display.")
            self.status_var.set("Persistent override removed.")
        else:
            messagebox.showerror("Error", f"Could not remove it:\n{result.message}")

    # -- live test (X11) ---------------------------------------------------------

    def _xrandr(self, *args):
        return subprocess.run(['xrandr'] + list(args), capture_output=True,
                              universal_newlines=True,
                              env=hostenv.subprocess_env())

    def _current_mode(self, out):
        """(mode_name, rate) currently active on `out`, or None."""
        res = self._xrandr('-q')
        if res.returncode != 0:
            return None
        in_block = False
        for line in res.stdout.splitlines():
            if not line.startswith((' ', '\t')):
                in_block = line.split()[0] == out if line.split() else False
                continue
            if in_block and '*' in line:
                tokens = line.split()
                mode = tokens[0]
                for tok in tokens[1:]:
                    if '*' in tok:
                        return mode, tok.replace('*', '').replace('+', '')
        return None

    def _cleanup_test_mode(self, out, name):
        self._xrandr('--delmode', out, name)
        self._xrandr('--rmmode', name)

    def _wayland_testable(self):
        """True when the compositor can apply the selected timings itself."""
        return (self.env.session_type == "wayland"
                and detect.compositor_can_apply(self.env, self.standard_var.get()))

    def test_mode(self):
        try:
            name, ml = self.current_modeline()
        except ValueError as e:
            messagebox.showerror("Error", f"Invalid input: {e}")
            return
        out = self.display_var.get()

        if self.env.session_type == "x11":
            self._test_mode_x11(out, name + "_test", ml)
        elif self._wayland_testable():
            if self.env.compositor == "kwin":
                self._test_mode_kwin(out)
            else:
                self._test_mode_wlroots(out, ml)
        else:
            self._test_mode_edid(out, ml)

    def _test_mode_x11(self, out, name, ml):
        previous = self._current_mode(out)

        res = self._xrandr('--newmode', name, *ml.xrandr_args())
        if res.returncode != 0 and 'already' not in res.stderr.lower():
            messagebox.showerror("Error", f"xrandr --newmode failed:\n{res.stderr}")
            return

        res = self._xrandr('--addmode', out, name)
        if res.returncode != 0:
            self._xrandr('--rmmode', name)
            hint = ""
            if self.env.has_nvidia_proprietary:
                hint = ("\n\nOn NVIDIA this usually means ModeValidation is not relaxed "
                        "yet: click Apply Configuration first, restart X, then test.")
            messagebox.showerror("Error", f"xrandr --addmode failed (driver rejected the "
                                          f"mode):\n{res.stderr}{hint}")
            return

        res = self._xrandr('--output', out, '--mode', name)
        if res.returncode != 0:
            self._cleanup_test_mode(out, name)
            messagebox.showerror(
                "Error",
                f"Could not switch to the mode:\n{res.stderr}\n\n"
                "'Configure crtc failed' usually means the pixel clock is over the "
                "link or EDID limit. Try CVT-RBv2 or a lower refresh rate, and "
                "check dmesg.")
            return

        def revert():
            if previous:
                self._xrandr('--output', out, '--mode', previous[0],
                             '--rate', previous[1])
            else:
                self._xrandr('--output', out, '--auto')
            self._cleanup_test_mode(out, name)

        self._show_revert_dialog(
            out, revert,
            keep_status="Mode kept for this session. Use Apply Configuration "
                        "to keep it after a restart.")

    def _test_mode_kwin(self, out):
        w, h, r = self.read_inputs()
        state = wayland.kwin_state(out)
        if not state:
            messagebox.showerror("Error", "Could not read the display configuration.")
            return
        previous_id, _ = state

        reduced = self.standard_var.get() != "cvt"
        new_id, err = wayland.kwin_add_custom_mode(out, w, h, r, reduced)
        if not new_id:
            messagebox.showerror("Error", f"Could not add the mode:\n{err}")
            return

        ok, msg = wayland.kwin_set_mode(out, new_id)
        if not ok:
            wayland.kwin_remove_custom_mode(out, w, h)
            messagebox.showerror("Error", f"Could not switch to the mode:\n{msg}")
            return

        def revert():
            wayland.kwin_set_mode(out, previous_id)
            wayland.kwin_remove_custom_mode(out, w, h)

        self._show_revert_dialog(
            out, revert,
            keep_status="Mode kept. KDE saves display settings automatically.")

    def _test_mode_wlroots(self, out, ml):
        w, h, r = self.read_inputs()
        previous = detect.current_mode(self.env, out)

        if self.env.compositor == "sway":
            apply_modeline, set_mode = wayland.sway_apply_modeline, wayland.sway_set_mode
        else:
            apply_modeline, set_mode = (wayland.hyprland_apply_modeline,
                                        wayland.hyprland_set_mode)

        ok, msg = apply_modeline(out, ml.timing_string())
        if not ok:
            messagebox.showerror(
                "Error",
                f"The compositor rejected the mode:\n{msg}\n\n"
                "The driver may not accept these timings. Check the compositor "
                "log and dmesg.")
            return

        def revert():
            if previous:
                pw, ph, pr = previous
                set_mode(out, pw, ph, pr)

        self._show_revert_dialog(
            out, revert,
            keep_status="Mode applied for this session. To keep it permanently, "
                        "add the line from the preview to your compositor config.")

    @staticmethod
    def _center_dialog(dialog):
        """Size the window to its content and centre it on the parent."""
        dialog.update_idletasks()
        w = dialog.winfo_reqwidth()
        h = dialog.winfo_reqheight()
        parent = dialog.master
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 3
        dialog.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")

    def _show_revert_dialog(self, out, on_revert, keep_status,
                            on_keep=None, message=None):
        dialog = tk.Toplevel(self.root)
        dialog.title("Testing mode")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.protocol("WM_DELETE_WINDOW", lambda: None)

        remaining = tk.IntVar(value=TEST_REVERT_SECONDS)
        # wrap the text and let the window size to fit it, rather than
        # forcing a width that clips longer messages
        label = ttk.Label(dialog, justify="center", wraplength=440, text="")
        label.pack(padx=16, pady=12)

        state = {"done": False}

        def revert():
            if state["done"]:
                return
            state["done"] = True
            on_revert()
            dialog.destroy()
            self.status_var.set("Reverted to previous mode.")

        def keep():
            if state["done"]:
                return
            state["done"] = True
            if on_keep:
                on_keep()
            dialog.destroy()
            self.status_var.set(keep_status)

        head = message or f"Testing new mode on {out}."

        def tick():
            if state["done"]:
                return
            n = remaining.get()
            label.config(text=f"{head}\n\n"
                              f"Reverting in {n} seconds.\n"
                              f"Press Enter or click Keep to keep it.")
            if n <= 0:
                revert()
                return
            remaining.set(n - 1)
            dialog.after(1000, tick)

        btns = ttk.Frame(dialog)
        btns.pack(pady=(0, 12))
        keep_btn = ttk.Button(btns, text="Keep", command=keep)
        keep_btn.pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text="Revert now", command=revert).pack(side=tk.LEFT, padx=8)
        dialog.bind('<Return>', lambda e: keep())
        keep_btn.focus()
        tick()
        self._center_dialog(dialog)

    # -- persist (X11) -------------------------------------------------------------

    def apply_configuration(self):
        if self.env.session_type != "x11":
            if self._wayland_testable():
                if self.env.compositor == "kwin":
                    messagebox.showinfo(
                        "Wayland",
                        "Use Test Mode to apply the mode. If you keep it,\n"
                        "KDE saves it automatically.")
                else:
                    messagebox.showinfo(
                        "Wayland",
                        "Use Test Mode to apply the mode now. To keep it\n"
                        "permanently, add the line from the preview to your\n"
                        "compositor config file.")
            else:
                try:
                    _, ml = self.current_modeline()
                except ValueError as e:
                    messagebox.showerror("Error", f"Invalid input: {e}")
                    return
                self._apply_edid_override(self.display_var.get(), ml)
            return
        try:
            name, ml = self.current_modeline()
        except ValueError as e:
            messagebox.showerror("Error", f"Invalid input: {e}")
            return
        out = self.display_var.get()
        config = self.build_xorg_config(out, name, ml)

        tmp_dir = f"/tmp/linux_cru_{os.getpid()}"
        os.makedirs(tmp_dir, exist_ok=True)
        conf_path = os.path.join(tmp_dir, "10-linux-cru.conf")
        with open(conf_path, 'w') as f:
            f.write(config)

        script_path = os.path.join(tmp_dir, "apply.sh")
        with open(script_path, 'w') as f:
            f.write("#!/bin/bash\n"
                    "set -e\n"
                    "mkdir -p /etc/X11/xorg.conf.d\n"
                    f"cp '{conf_path}' /etc/X11/xorg.conf.d/10-linux-cru.conf\n"
                    "chmod 644 /etc/X11/xorg.conf.d/10-linux-cru.conf\n")
        os.chmod(script_path, 0o755)

        result = privileged.run_as_root(['/bin/bash', script_path],
                                        ask_password=self._ask_password)
        try:
            import shutil
            shutil.rmtree(tmp_dir)
        except OSError:
            pass

        if result.cancelled:
            self.status_var.set("Cancelled.")
        elif result.ok:
            messagebox.showinfo(
                "Success",
                "Configuration saved to /etc/X11/xorg.conf.d/10-linux-cru.conf.\n\n"
                "The new mode will be available after you restart X\n"
                "(log out and back in). To undo, delete that file.")
            self.status_var.set("Configuration applied successfully.")
        else:
            messagebox.showerror("Error",
                                 f"Failed to apply configuration:\n{result.message}")
            self.status_var.set("Error: Failed to apply configuration")


def self_test():
    """Build the whole interface once and exit. Used to check a build."""
    root = tk.Tk()
    root.withdraw()
    gui = LinuxCRU(root)
    assert gui.preview_text.get("1.0", "end").strip(), "the preview is empty"
    assert gui.displays, "no displays were listed"
    for standard in timings.STANDARDS:
        gui.standard_var.set(standard)
        gui.generate_preview()
        assert gui.status_var.get(), f"no status for {standard}"
    root.destroy()
    print(f"self-test passed: {gui.env.session_type} session, "
          f"{gui.env.compositor}, displays: {', '.join(gui.displays)}")


def main():
    if "--self-test" in sys.argv:
        self_test()
        return
    if "--version" in sys.argv:
        import linux_cru
        print(f"Linux CRU {linux_cru.__version__}")
        return
    root = tk.Tk()
    LinuxCRU(root)
    root.mainloop()


if __name__ == "__main__":
    main()
