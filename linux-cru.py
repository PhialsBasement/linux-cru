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
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from linux_cru import detect, timings

TEST_REVERT_SECONDS = 15


def run_with_sudo(command, work_dir=None):
    """Run a command as root via pkexec (graphical auth prompt)."""
    try:
        process = subprocess.Popen(['pkexec'] + command,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE,
                                   cwd=work_dir)
        output, error = process.communicate()
        if process.returncode == 0:
            return True, output.decode()
        return False, error.decode()
    except Exception as e:
        return False, str(e)


class LinuxCRU:
    def __init__(self, root):
        self.root = root
        self.root.title("Linux Custom Resolution Utility")
        self.root.geometry("860x780")
        self.root.minsize(640, 560)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        self.env = detect.detect()

        self.main_frame = ttk.Frame(root, padding="10")
        self.main_frame.grid(row=0, column=0, sticky="nsew")
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(4, weight=1)  # preview grows

        self.create_environment_section()
        self.create_display_section()
        self.create_resolution_section()
        self.create_timing_section()
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

    def get_displays(self):
        """Output names: xrandr names on X11, DRM connector names on Wayland."""
        if self.env.session_type == "x11":
            names = []
            try:
                out = subprocess.check_output(['xrandr', '-q'], universal_newlines=True,
                                              stderr=subprocess.DEVNULL)
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

    def load_current_settings(self, quiet=True):
        """Fill the inputs with the selected display's active mode."""
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
        ]
        for i, (label, value) in enumerate(options):
            ttk.Radiobutton(frame, text=label, variable=self.standard_var, value=value,
                            command=self.generate_preview).grid(row=i, column=0,
                                                                sticky="w", padx=5)

        if self.env.has_nvidia_proprietary and self.env.session_type == "x11":
            self.relax_validation = tk.BooleanVar(value=False)
            ttk.Checkbutton(
                frame,
                text="Skip NVIDIA EDID and pixel clock checks (needed for overclocking)",
                variable=self.relax_validation,
                command=self.generate_preview).grid(row=len(options), column=0,
                                                    sticky="w", padx=5, pady=(6, 0))
        else:
            self.relax_validation = tk.BooleanVar(value=False)

    # -- preview -------------------------------------------------------------------

    def create_preview_section(self):
        frame = ttk.LabelFrame(self.main_frame, text="Configuration Preview", padding=5)
        frame.grid(row=4, column=0, sticky="nsew", pady=(0, 10))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        self.preview_text = tk.Text(frame, height=14, wrap=tk.NONE)
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

        if self.env.session_type == "x11":
            body = self.build_x11_preview(out, name, ml)
        elif self.env.session_type == "wayland":
            body = self.build_wayland_preview(out, name, ml)
        else:
            body = "Could not detect the session type. Showing the modeline only.\n"

        self.preview_text.delete(1.0, tk.END)
        self.preview_text.insert(1.0, header + body)
        self.status_var.set("Configuration generated. Use Test Mode to try it, then "
                            "Apply Configuration to save it."
                            if self.env.session_type == "x11"
                            else "Configuration generated. Run the commands shown in the preview.")

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

        if env.has_nvidia_proprietary:
            return self._edid_override_notes(out, name, ml,
                reason="The NVIDIA driver does not accept custom modes from Wayland "
                       "compositors.")

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
            if env.kde_custom_modes_available:
                mhz = int(round(r * 1000))
                if self.standard_var.get() == "cvt":
                    blanking, kwin_std = "full", "cvt"
                else:
                    blanking, kwin_std = "reduced", "cvt-rb"
                kwin_ml = timings.calc(w, h, r, kwin_std)
                lines = [
                    "# KWin takes only resolution and refresh rate, then computes the\n"
                    "# timings itself. Run both commands (adding and switching are\n"
                    "# separate steps):\n",
                    f"kscreen-doctor output.{out}.addCustomMode.{w}.{h}.{mhz}.{blanking}\n",
                    f"kscreen-doctor output.{out}.mode.{w}x{h}@{r:g}\n\n",
                    f"# KWin will generate these timings ({kwin_std.upper()}):\n",
                    f"# {kwin_ml.xorg_modeline(name)}\n",
                ]
                if self.standard_var.get() == "cvt-rb2":
                    lines.append(
                        "# KWin cannot generate CVT-RBv2 timings. To use the RBv2\n"
                        "# timings shown at the top, you need an EDID override.\n")
                return "".join(lines)
            return self._edid_override_notes(out, name, ml,
                reason=f"KDE Plasma "
                       f"{'.'.join(map(str, env.compositor_version[:2])) or '?'} "
                       "cannot add custom modes. That needs Plasma 6.6 or newer.")

        if env.is_wlroots_family:
            return ("# Run now:\n"
                    f"wlr-randr --output {out} --custom-mode {w}x{h}@{r:g}Hz\n\n"
                    "# The compositor computes CVT full-blanking timings for this.\n"
                    "# For exact custom timings, use an EDID override.\n")

        return self._edid_override_notes(out, name, ml,
            reason=f"{env.compositor} cannot add custom modes.")

    def _edid_override_notes(self, out, name, ml, reason):
        conn = self._drm_connector_for(out)
        short = self._strip_card(conn)
        tool = self.env.initramfs_tool or "your initramfs tool"
        return (f"# {reason}\n"
                "# Adding this mode requires an EDID override. Steps:\n"
                "#   1. Dump the current EDID:\n"
                f"#        cat /sys/class/drm/{conn}/edid > mon.bin\n"
                "#   2. Add this timing to it as a detailed timing block\n"
                "#      (wxEDID, or export from Windows CRU):\n"
                f"#        {ml.xorg_modeline(name)}\n"
                "#   3. Install the file:\n"
                "#        sudo install -Dm644 custom.bin /usr/lib/firmware/edid/custom.bin\n"
                "#   4. Add to the kernel command line:\n"
                f"#        drm.edid_firmware={short}:edid/custom.bin\n"
                f"#   5. Add the file to the initramfs ({tool}) and reboot.\n"
                "# To test without rebooting (as root):\n"
                f"#   cat custom.bin > /sys/kernel/debug/dri/<N>/{short}/edid_override\n"
                f"#   echo 1 > /sys/kernel/debug/dri/<N>/{short}/trigger_hotplug\n"
                "# Full details: docs/RESEARCH.md in the linux-cru repo.\n")

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
        frame.grid(row=5, column=0, sticky="ew", pady=(0, 5))
        frame.grid_columnconfigure(1, weight=1)

        ttk.Button(frame, text="Generate Preview",
                   command=self.generate_preview).grid(row=0, column=0, padx=5)

        self.test_btn = ttk.Button(frame, text=f"Test Mode ({TEST_REVERT_SECONDS}s auto-revert)",
                                   command=self.test_mode)
        self.test_btn.grid(row=0, column=2, padx=5)

        self.apply_btn = ttk.Button(frame, text="Apply Configuration",
                                    command=self.apply_configuration)
        self.apply_btn.grid(row=0, column=3, padx=5)

        if self.env.session_type != "x11":
            self.test_btn.state(["disabled"])

        self.status_var = tk.StringVar()
        ttk.Label(self.main_frame, textvariable=self.status_var,
                  wraplength=780).grid(row=6, column=0, sticky="ew", pady=5)

    # -- live test (X11) ---------------------------------------------------------

    def _xrandr(self, *args):
        return subprocess.run(['xrandr'] + list(args), capture_output=True,
                              universal_newlines=True)

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

    def test_mode(self):
        if self.env.session_type != "x11":
            messagebox.showinfo("X11 only",
                                "Live testing via xrandr only works on X11.\n"
                                "Use the compositor commands from the preview instead.")
            return
        try:
            name, ml = self.current_modeline()
        except ValueError as e:
            messagebox.showerror("Error", f"Invalid input: {e}")
            return
        name += "_test"
        out = self.display_var.get()
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

        self._show_revert_dialog(out, name, previous)

    def _show_revert_dialog(self, out, name, previous):
        dialog = tk.Toplevel(self.root)
        dialog.title("Testing mode")
        dialog.geometry("420x140")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.protocol("WM_DELETE_WINDOW", lambda: None)

        remaining = tk.IntVar(value=TEST_REVERT_SECONDS)
        label = ttk.Label(dialog, justify="center",
                          text="")
        label.pack(pady=12)

        state = {"done": False}

        def revert():
            if state["done"]:
                return
            state["done"] = True
            if previous:
                self._xrandr('--output', out, '--mode', previous[0], '--rate', previous[1])
            else:
                self._xrandr('--output', out, '--auto')
            self._cleanup_test_mode(out, name)
            dialog.destroy()
            self.status_var.set("Reverted to previous mode.")

        def keep():
            if state["done"]:
                return
            state["done"] = True
            dialog.destroy()
            self.status_var.set("Mode kept for this session. Use Apply Configuration "
                                "to keep it after a restart.")

        def tick():
            if state["done"]:
                return
            n = remaining.get()
            label.config(text=f"Testing new mode on {out}.\n\n"
                              f"Reverting in {n} seconds.\n"
                              f"Press Enter or click Keep to keep it.")
            if n <= 0:
                revert()
                return
            remaining.set(n - 1)
            dialog.after(1000, tick)

        btns = ttk.Frame(dialog)
        btns.pack(pady=6)
        keep_btn = ttk.Button(btns, text="Keep", command=keep)
        keep_btn.pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text="Revert now", command=revert).pack(side=tk.LEFT, padx=8)
        dialog.bind('<Return>', lambda e: keep())
        keep_btn.focus()
        tick()

    # -- persist (X11) -------------------------------------------------------------

    def apply_configuration(self):
        if self.env.session_type != "x11":
            messagebox.showinfo(
                "Wayland",
                "On Wayland, apply the mode with the commands shown in the preview.\n"
                "Applying directly from this tool is not supported yet.")
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

        success, message = run_with_sudo(['/bin/bash', script_path])
        try:
            import shutil
            shutil.rmtree(tmp_dir)
        except OSError:
            pass

        if success:
            messagebox.showinfo(
                "Success",
                "Configuration saved to /etc/X11/xorg.conf.d/10-linux-cru.conf.\n\n"
                "The new mode will be available after you restart X\n"
                "(log out and back in). To undo, delete that file.")
            self.status_var.set("Configuration applied successfully.")
        else:
            messagebox.showerror("Error", f"Failed to apply configuration:\n{message}")
            self.status_var.set("Error: Failed to apply configuration")


def main():
    root = tk.Tk()
    LinuxCRU(root)
    root.mainloop()


if __name__ == "__main__":
    main()
