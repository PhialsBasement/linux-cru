#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import os
import re
from pathlib import Path
import sys
import tempfile
import shutil
from datetime import datetime
import json
import dbus
from enum import Enum, auto

class DisplayServer(Enum):
    X11 = auto()
    WAYLAND = auto()
    UNKNOWN = auto()

class WaylandCompositor(Enum):
    SWAY = auto()
    KDE = auto()
    GNOME = auto()
    HYPRLAND = auto()
    UNKNOWN = auto()

class DisplayManager:
    def __init__(self):
        self.display_server = self._detect_display_server()
        self.wayland_compositor = self._detect_wayland_compositor() if self.display_server == DisplayServer.WAYLAND else None

    def _detect_display_server(self):
        wayland_display = os.environ.get('WAYLAND_DISPLAY')
        if wayland_display:
            return DisplayServer.WAYLAND
        elif os.environ.get('DISPLAY'):
            return DisplayServer.X11
        return DisplayServer.UNKNOWN

    def _detect_wayland_compositor(self):
        xdg_session_type = os.environ.get('XDG_SESSION_TYPE')
        if xdg_session_type != 'wayland':
            return WaylandCompositor.UNKNOWN

        # Check for specific compositors
        if os.environ.get('SWAYSOCK'):
            return WaylandCompositor.SWAY
        elif os.environ.get('KDE_FULL_SESSION'):
            return WaylandCompositor.KDE
        elif os.environ.get('GNOME_SHELL_SESSION_MODE'):
            return WaylandCompositor.GNOME
        elif os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
            return WaylandCompositor.HYPRLAND

        return WaylandCompositor.UNKNOWN

    def get_displays(self):
        """Get list of connected displays based on current display server"""
        if self.display_server == DisplayServer.X11:
            return self._get_x11_displays()
        elif self.display_server == DisplayServer.WAYLAND:
            return self._get_wayland_displays()
        return ["HDMI-0"]  # Fallback

    def _get_x11_displays(self):
        try:
            output = subprocess.check_output(['xrandr', '-q'],
                                          universal_newlines=True,
                                          stderr=subprocess.PIPE)

            displays = []
            for line in output.splitlines():
                if ' connected ' in line and not line.startswith('+'):
                    display = line.split()[0]
                    if display not in displays:
                        displays.append(display)

            return displays if displays else ["HDMI-0"]
        except subprocess.CalledProcessError:
            return ["HDMI-0"]

    def _get_wayland_displays(self):
        if self.wayland_compositor == WaylandCompositor.SWAY:
            return self._get_sway_displays()
        elif self.wayland_compositor == WaylandCompositor.KDE:
            return self._get_kde_displays()
        elif self.wayland_compositor == WaylandCompositor.GNOME:
            return self._get_gnome_displays()
        elif self.wayland_compositor == WaylandCompositor.HYPRLAND:
            return self._get_hyprland_displays()
        return ["HDMI-0"]

    def _get_sway_displays(self):
        try:
            output = subprocess.check_output(['swaymsg', '-t', 'get_outputs'],
                                          universal_newlines=True)
            outputs = json.loads(output)
            return [output['name'] for output in outputs if output['active']]
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return ["HDMI-0"]

    def _get_kde_displays(self):
        try:
            bus = dbus.SessionBus()
            kscreen = bus.get_object('org.kde.KScreen', '/backend')
            outputs = kscreen.getOutputs()
            return [output['name'] for output in outputs if output['connected']]
        except Exception:
            return ["HDMI-0"]

    def _get_gnome_displays(self):
        try:
            bus = dbus.SessionBus()
            mutter = bus.get_object('org.gnome.Mutter.DisplayConfig',
                                  '/org/gnome/Mutter/DisplayConfig')
            displays = mutter.GetResources()
            return [display['connector'] for display in displays if display['is_connected']]
        except Exception:
            return ["HDMI-0"]

    def _get_hyprland_displays(self):
        try:
            output = subprocess.check_output(['hyprctl', 'monitors', '-j'],
                                          universal_newlines=True)
            monitors = json.loads(output)
            return [monitor['name'] for monitor in monitors]
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return ["HDMI-0"]

class SudoPrompt:
    def __init__(self, parent):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Authentication Required")
        self.dialog.geometry("300x150")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        if parent:
            x = parent.winfo_x() + (parent.winfo_width() // 2) - (300 // 2)
            y = parent.winfo_y() + (parent.winfo_height() // 2) - (150 // 2)
            self.dialog.geometry(f"+{x}+{y}")

        style = ttk.Style()
        style.configure("Auth.TLabel", font=("TkDefaultFont", 10))

        ttk.Label(self.dialog,
                 text="Administrator privileges are required\nto modify display settings.",
                 style="Auth.TLabel",
                 justify="center").pack(pady=10)

        self.password = tk.StringVar()
        self.entry = ttk.Entry(self.dialog, show="●", textvariable=self.password)
        self.entry.pack(pady=10, padx=20, fill=tk.X)

        btn_frame = ttk.Frame(self.dialog)
        btn_frame.pack(fill=tk.X, pady=10, padx=20)

        ttk.Button(btn_frame, text="OK",
                  command=self.dialog.quit).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="Cancel",
                  command=self.cancel).pack(side=tk.RIGHT, padx=5)

        self.entry.bind('<Return>', lambda e: self.dialog.quit())
        self.entry.focus()

    def cancel(self):
        self.password.set("")
        self.dialog.quit()

def run_with_sudo(command, work_dir=None):
    try:
        # First try pkexec
        cmd = ['pkexec'] + command
        process = subprocess.Popen(cmd,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 cwd=work_dir)
        output, error = process.communicate()

        if process.returncode == 0:
            return True, output.decode()

        # If pkexec fails, try with graphical sudo alternatives
        for sudo_cmd in ['gksudo', 'kdesu', 'beesu']:
            try:
                cmd = [sudo_cmd] + command
                process = subprocess.Popen(cmd,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE,
                                        cwd=work_dir)
                output, error = process.communicate()

                if process.returncode == 0:
                    return True, output.decode()
            except FileNotFoundError:
                continue

        return False, error.decode()
    except Exception as e:
        return False, str(e)

class LinuxCRU:
    def __init__(self, root):
        self.root = root
        self.root.title("Linux Custom Resolution Utility")
        self.display_manager = DisplayManager()

        # Set window size and make it resizable
        self.root.geometry("800x700")
        self.root.minsize(600, 500)

        # Configure grid weights for resizing
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        # Set window icon if running as AppImage
        if getattr(sys, 'frozen', False):
            icon_path = os.path.join(os.path.dirname(sys.executable),
                                   'usr/share/icons/hicolor/256x256/apps/linux_cru.png')
            if os.path.exists(icon_path):
                img = tk.PhotoImage(file=icon_path)
                self.root.tk.call('wm', 'iconphoto', self.root._w, img)

        # Create main container with padding
        self.main_frame = ttk.Frame(root, padding="10")
        self.main_frame.grid(row=0, column=0, sticky="nsew")
        self.main_frame.grid_columnconfigure(0, weight=1)

        # Style configuration
        style = ttk.Style()
        style.configure('Header.TLabel', font=('TkDefaultFont', 12, 'bold'))

        # Create the interface sections
        self.create_display_section()
        self.create_resolution_section()
        self.create_advanced_section()
        self.create_preview_section()
        self.create_action_section()

        # Initialize state
        self.last_valid_width = "1280"
        self.last_valid_height = "1024"
        self.last_valid_refresh = "165"

        # Generate initial preview
        self.generate_preview()

        # Bind validation and preview update
        self.bind_validators()

    def create_display_section(self):
        display_frame = ttk.LabelFrame(self.main_frame, text="Display Selection", padding=5)
        display_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        # Add display server info
        server_text = f"Display Server: {self.display_manager.display_server.name}"
        if self.display_manager.display_server == DisplayServer.WAYLAND:
            server_text += f" ({self.display_manager.wayland_compositor.name})"

        ttk.Label(display_frame, text=server_text).grid(row=0, column=0, sticky="w", padx=5, pady=5)

        self.displays = self.display_manager.get_displays()
        self.display_var = tk.StringVar(value=self.displays[0] if self.displays else "")

        display_combo = ttk.Combobox(display_frame,
                                   textvariable=self.display_var,
                                   values=self.displays,
                                   state="readonly")
        display_combo.grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        display_frame.grid_columnconfigure(0, weight=1)

        display_combo.bind('<<ComboboxSelected>>', lambda e: self.generate_preview())

    def create_resolution_section(self):
        res_frame = ttk.LabelFrame(self.main_frame, text="Resolution Settings", padding=5)
        res_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        res_frame.grid_columnconfigure(1, weight=1)

        # Resolution inputs
        ttk.Label(res_frame, text="Width:").grid(row=0, column=0, padx=5, pady=5)
        self.width_var = tk.StringVar(value="1280")
        width_entry = ttk.Entry(res_frame, textvariable=self.width_var, width=8)
        width_entry.grid(row=0, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(res_frame, text="Height:").grid(row=1, column=0, padx=5, pady=5)
        self.height_var = tk.StringVar(value="1024")
        height_entry = ttk.Entry(res_frame, textvariable=self.height_var, width=8)
        height_entry.grid(row=1, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(res_frame, text="Refresh Rate:").grid(row=2, column=0, padx=5, pady=5)
        self.refresh_var = tk.StringVar(value="165")
        refresh_entry = ttk.Entry(res_frame, textvariable=self.refresh_var, width=8)
        refresh_entry.grid(row=2, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(res_frame, text="pixels").grid(row=0, column=2, sticky="w", padx=5)
        ttk.Label(res_frame, text="pixels").grid(row=1, column=2, sticky="w", padx=5)
        ttk.Label(res_frame, text="Hz").grid(row=2, column=2, sticky="w", padx=5)

    def create_advanced_section(self):
        adv_frame = ttk.LabelFrame(self.main_frame, text="Advanced Settings", padding=5)
        adv_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        adv_frame.grid_columnconfigure(0, weight=1)

        # Create a sub-frame for options to control layout
        options_frame = ttk.Frame(adv_frame)
        options_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        options_frame.grid_columnconfigure(0, weight=1)

        # Timing options
        self.reduced_blanking = tk.BooleanVar(value=True)
        rb_check = ttk.Checkbutton(options_frame,
                                 text="Use Reduced Blanking (recommended for high refresh rates)",
                                 variable=self.reduced_blanking,
                                 command=self.generate_preview)
        rb_check.grid(row=0, column=0, sticky="w", pady=(0, 5))

        self.force_enable = tk.BooleanVar(value=True)
        fe_check = ttk.Checkbutton(options_frame,
                                 text="Force Enable Mode (override EDID restrictions)",
                                 variable=self.force_enable,
                                 command=self.generate_preview)
        fe_check.grid(row=1, column=0, sticky="w")

    def create_preview_section(self):
        preview_frame = ttk.LabelFrame(self.main_frame, text="Configuration Preview", padding=5)
        preview_frame.grid(row=3, column=0, sticky="nsew", pady=(0, 10))
        preview_frame.grid_columnconfigure(0, weight=1)
        preview_frame.grid_rowconfigure(0, weight=1)

        self.preview_text = tk.Text(preview_frame, height=12, wrap=tk.NONE)
        self.preview_text.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        # Add scrollbars
        y_scroll = ttk.Scrollbar(preview_frame, orient="vertical",
                               command=self.preview_text.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(preview_frame, orient="horizontal",
                               command=self.preview_text.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")

        self.preview_text.configure(yscrollcommand=y_scroll.set,
                                  xscrollcommand=x_scroll.set)

    def create_action_section(self):
        button_frame = ttk.Frame(self.main_frame)
        button_frame.grid(row=4, column=0, sticky="ew", pady=(0, 5))
        button_frame.grid_columnconfigure(1, weight=1)

        ttk.Button(button_frame,
                  text="Generate Preview",
                  command=self.generate_preview).grid(row=0, column=0, padx=5)

        ttk.Button(button_frame,
                  text="Apply Configuration",
                  command=self.apply_configuration).grid(row=0, column=2, padx=5)

        # Status label
        self.status_var = tk.StringVar()
        status_label = ttk.Label(self.main_frame,
                               textvariable=self.status_var,
                               wraplength=600)
        status_label.grid(row=5, column=0, sticky="ew", pady=5)

    def bind_validators(self):
        def validate_number(var, old_value):
            def callback(*args):
                try:
                    value = var.get().strip()
                    if value:
                        int_val = int(value)
                        if int_val <= 0:
                            var.set(old_value)
                        else:
                            self.generate_preview()
                except ValueError:
                    var.set(old_value)
            return callback

        self.width_var.trace_add("write", validate_number(self.width_var, self.last_valid_width))
        self.height_var.trace_add("write", validate_number(self.height_var, self.last_valid_height))
        self.refresh_var.trace_add("write", validate_number(self.refresh_var, self.last_valid_refresh))

    def calculate_modeline(self):
        """Calculate modeline parameters based on resolution and refresh rate"""
        width = int(self.width_var.get())
        height = int(self.height_var.get())
        refresh = float(self.refresh_var.get())

        if self.reduced_blanking.get():
            # Conservative reduced blanking parameters
            h_front = max(16, width // 100)
            h_sync = max(32, width // 80)
            h_back = max(48, width // 50)

            v_front = 1
            v_sync = 1
            v_back = max(3, height // 200)

            h_total = width + h_front + h_sync + h_back
            v_total = height + v_front + v_sync + v_back

            pixel_clock = h_total * v_total * refresh / 1000000  # MHz

            return (f"{pixel_clock:.2f} {width} {width + h_front} "
                   f"{width + h_front + h_sync} {h_total} "
                   f"{height} {height + v_front} "
                   f"{height + v_front + v_sync} {v_total} "
                   f"-HSync +VSync")
        else:
            # Standard CVT parameters
            try:
                cvt = subprocess.check_output(
                    ['cvt', str(width), str(height), str(refresh)],
                    universal_newlines=True
                )
                modeline = re.search(r'Modeline.*"(.*)"(.*)', cvt)
                if modeline:
                    return modeline.group(2).strip()
            except subprocess.CalledProcessError:
                return None

    def generate_preview(self):
        """Generate configuration preview based on display server"""
        try:
            mode_name = f"{self.width_var.get()}x{self.height_var.get()}_{self.refresh_var.get()}"
            modeline = self.calculate_modeline()

            if not modeline:
                raise ValueError("Failed to calculate modeline parameters")

            if self.display_manager.display_server == DisplayServer.X11:
                config = self._generate_x11_config(mode_name, modeline)
            else:
                config = self._generate_wayland_config(mode_name, modeline)

            self.preview_text.delete(1.0, tk.END)
            self.preview_text.insert(1.0, config)

            self.status_var.set("Configuration generated successfully. Click 'Apply Configuration' to use these settings.")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to generate configuration: {str(e)}")
            self.status_var.set("Error: Failed to generate configuration")

    def _generate_x11_config(self, mode_name, modeline):
        """Generate X11 configuration"""
        force_options = """
    Option "ModeValidation" "AllowNonEdidModes,NoMaxPClkCheck,NoEdidMaxPClkCheck,NoMaxSizeCheck,NoHorizSyncCheck,NoVertRefreshCheck"
    Option "IgnoreEDID" "True\"""" if self.force_enable.get() else ""

        return f"""# Generated by Linux CRU on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Section "Monitor"
    Identifier "{self.display_var.get()}"
    Option "PreferredMode" "{mode_name}"
    Modeline "{mode_name}" {modeline}
    Option "ExactModeTimingsDVI" "True"{force_options}
EndSection

Section "Screen"
    Identifier "Screen0"
    Device "Device0"
    Monitor "{self.display_var.get()}"
    Option "AllowIndirectGLXProtocol" "off"
    Option "TripleBuffer" "on"
EndSection

# Kernel module configuration (/etc/modprobe.d/nvidia.conf):
options nvidia NVreg_RegistryDwords="CustomEDID={mode_name};EnableBrightnessControl=1"
"""

    def _generate_wayland_config(self, mode_name, modeline):
        """Generate Wayland configuration based on compositor"""
        if self.display_manager.wayland_compositor == WaylandCompositor.SWAY:
            return self._generate_sway_config(mode_name, modeline)
        elif self.display_manager.wayland_compositor == WaylandCompositor.KDE:
            return self._generate_kde_config(mode_name, modeline)
        elif self.display_manager.wayland_compositor == WaylandCompositor.GNOME:
            return self._generate_gnome_config(mode_name, modeline)
        elif self.display_manager.wayland_compositor == WaylandCompositor.HYPRLAND:
            return self._generate_hyprland_config(mode_name, modeline)
        else:
            return "# Unsupported Wayland compositor"

    def _generate_sway_config(self, mode_name, modeline):
        """Generate Sway-specific configuration"""
        width = self.width_var.get()
        height = self.height_var.get()
        refresh = self.refresh_var.get()

        return f"""# Generated by Linux CRU on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Add to ~/.config/sway/config:

output {self.display_var.get()} {{
    mode {width}x{height}@{refresh}Hz
    custom_mode {width}x{height}@{refresh} {modeline}
}}

# Kernel module configuration (/etc/modprobe.d/nvidia.conf):
options nvidia NVreg_RegistryDwords="CustomEDID={mode_name};EnableBrightnessControl=1"
"""

    def _generate_kde_config(self, mode_name, modeline):
        """Generate KDE Plasma Wayland configuration"""
        width = self.width_var.get()
        height = self.height_var.get()
        refresh = self.refresh_var.get()

        return f"""# Generated by Linux CRU on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Add to ~/.config/plasma-workspace/env/custom_mode.sh:

#!/bin/sh
export KWIN_X11_REFRESH_RATE={refresh}
export KWIN_X11_PREFERRED_MODE={width}x{height}

# Add to /etc/modprobe.d/nvidia.conf:
options nvidia NVreg_RegistryDwords="CustomEDID={mode_name};EnableBrightnessControl=1"

# Note: Make the script executable:
# chmod +x ~/.config/plasma-workspace/env/custom_mode.sh
"""

    def _generate_gnome_config(self, mode_name, modeline):
        """Generate GNOME Wayland configuration"""
        width = self.width_var.get()
        height = self.height_var.get()
        refresh = self.refresh_var.get()

        return f"""# Generated by Linux CRU on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Add to /etc/gnome-shell/custom-modes.conf:

[custom-modes]
{self.display_var.get()}={width}x{height}@{refresh} {modeline}

# Add to /etc/modprobe.d/nvidia.conf:
options nvidia NVreg_RegistryDwords="CustomEDID={mode_name};EnableBrightnessControl=1"
"""

    def _generate_hyprland_config(self, mode_name, modeline):
        """Generate Hyprland configuration"""
        width = self.width_var.get()
        height = self.height_var.get()
        refresh = self.refresh_var.get()

        return f"""# Generated by Linux CRU on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Add to ~/.config/hypr/hyprland.conf:

monitor={self.display_var.get()},{width}x{height}@{refresh},auto,1
custom_mode={self.display_var.get()},{width}x{height}@{refresh},{modeline}

# Add to /etc/modprobe.d/nvidia.conf:
options nvidia NVreg_RegistryDwords="CustomEDID={mode_name};EnableBrightnessControl=1"
"""

    def apply_configuration(self):
        """Apply the configuration based on display server"""
        try:
            config = self.preview_text.get(1.0, tk.END)

            if self.display_manager.display_server == DisplayServer.X11:
                self._apply_x11_configuration(config)
            else:
                self._apply_wayland_configuration(config)

        except Exception as e:
            messagebox.showerror("Error",
                               f"Failed to apply configuration:\n{str(e)}\n\n"
                               "Make sure you have administrator privileges and "
                               "the required dependencies are installed.")
            self.status_var.set("Error: Failed to apply configuration")

    def _apply_x11_configuration(self, config):
        """Apply X11 configuration"""
        xorg_config = config.split("# Kernel module configuration")[0].strip()
        nvidia_config = "options nvidia " + config.split("options nvidia ")[1].strip()

        # Create temporary directory with random suffix for safety
        tmp_dir = f"/tmp/linux_cru_{os.getpid()}"
        os.makedirs(tmp_dir, exist_ok=True)

        try:
            # Write configuration files
            with open(f"{tmp_dir}/xorg.conf", 'w') as f:
                f.write(xorg_config)
            with open(f"{tmp_dir}/nvidia.conf", 'w') as f:
                f.write(nvidia_config)

            # Create helper script
            script_path = f"{tmp_dir}/apply_config.sh"
            with open(script_path, 'w') as f:
                f.write("""#!/bin/bash
set -e
cp "${1}/xorg.conf" /etc/X11/xorg.conf.d/10-custom-modes.conf
cp "${1}/nvidia.conf" /etc/modprobe.d/nvidia.conf
chmod 644 /etc/X11/xorg.conf.d/10-custom-modes.conf
chmod 644 /etc/modprobe.d/nvidia.conf
mkinitcpio -P
""")
            os.chmod(script_path, 0o755)

            # Run helper script with sudo
            success, message = run_with_sudo([script_path, tmp_dir])

            if success:
                restart = messagebox.askquestion("Success",
                                               "Configuration applied successfully.\n\n"
                                               "Would you like to restart the display manager now?\n"
                                               "(This will close all applications and log you out)",
                                               icon='info')
                if restart == 'yes':
                    run_with_sudo(['systemctl', 'restart', 'display-manager'])
                else:
                    messagebox.showinfo("Success",
                                      "Configuration saved. The changes will take effect\n"
                                      "after you restart your display manager or reboot.")
            else:
                raise Exception(message)

        finally:
            # Cleanup temporary files
            try:
                shutil.rmtree(tmp_dir)
            except:
                pass

    def _apply_wayland_configuration(self, config):
        """Apply Wayland configuration based on compositor"""
        if self.display_manager.wayland_compositor == WaylandCompositor.SWAY:
            self._apply_sway_configuration(config)
        elif self.display_manager.wayland_compositor == WaylandCompositor.KDE:
            self._apply_kde_configuration(config)
        elif self.display_manager.wayland_compositor == WaylandCompositor.GNOME:
            self._apply_gnome_configuration(config)
        elif self.display_manager.wayland_compositor == WaylandCompositor.HYPRLAND:
            self._apply_hyprland_configuration(config)
        else:
            raise Exception("Unsupported Wayland compositor")

    def _apply_sway_configuration(self, config):
        """Apply Sway configuration"""
        user_config_dir = os.path.expanduser("~/.config/sway")
        os.makedirs(user_config_dir, exist_ok=True)

        # Extract Sway config part
        sway_config = config.split("# Add to ~/.config/sway/config:")[1].split("# Kernel")[0].strip()
        nvidia_config = "options nvidia " + config.split("options nvidia ")[1].strip()

        # Update Sway config
        config_path = os.path.join(user_config_dir, "config.d", "custom-mode.conf")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)

        with open(config_path, 'w') as f:
            f.write(sway_config)

        # Update NVIDIA config
        success, _ = run_with_sudo(['bash', '-c', f'echo "{nvidia_config}" > /etc/modprobe.d/nvidia.conf'])
        if not success:
            raise Exception("Failed to update NVIDIA configuration")

        messagebox.showinfo("Success",
                          "Configuration saved. The changes will take effect\n"
                          "after reloading Sway (Mod+Shift+C) or restarting.")

    def _apply_kde_configuration(self, config):
        """Apply KDE Plasma Wayland configuration"""
        env_dir = os.path.expanduser("~/.config/plasma-workspace/env")
        os.makedirs(env_dir, exist_ok=True)

        # Extract KDE script part
        kde_script = config.split("#!/bin/sh")[1].split("# Add to /etc/modprobe.d")[0].strip()
        nvidia_config = "options nvidia " + config.split("options nvidia ")[1].strip()

        # Update KDE script
        script_path = os.path.join(env_dir, "custom_mode.sh")
        with open(script_path, 'w') as f:
            f.write("#!/bin/sh\n" + kde_script)
        os.chmod(script_path, 0o755)

        # Update NVIDIA config
        success, _ = run_with_sudo(['bash', '-c', f'echo "{nvidia_config}" > /etc/modprobe.d/nvidia.conf'])
        if not success:
            raise Exception("Failed to update NVIDIA configuration")

        messagebox.showinfo("Success",
                          "Configuration saved. The changes will take effect\n"
                          "after logging out and back in.")

    def _apply_gnome_configuration(self, config):
        """Apply GNOME Wayland configuration"""
        # Extract GNOME config part
        gnome_config = config.split("[custom-modes]")[1].split("# Add to /etc/modprobe.d")[0].strip()
        nvidia_config = "options nvidia " + config.split("options nvidia ")[1].strip()

        # Create temporary files
        tmp_dir = f"/tmp/linux_cru_{os.getpid()}"
        os.makedirs(tmp_dir, exist_ok=True)

        try:
            # Write configuration files
            with open(f"{tmp_dir}/custom-modes.conf", 'w') as f:
                f.write("[custom-modes]\n" + gnome_config)

            # Create helper script
            script_path = f"{tmp_dir}/apply_config.sh"
            with open(script_path, 'w') as f:
                f.write(f"""#!/bin/bash
set -e
mkdir -p /etc/gnome-shell
cp "{tmp_dir}/custom-modes.conf" /etc/gnome-shell/custom-modes.conf
echo "{nvidia_config}" > /etc/modprobe.d/nvidia.conf
chmod 644 /etc/gnome-shell/custom-modes.conf
chmod 644 /etc/modprobe.d/nvidia.conf
""")
            os.chmod(script_path, 0o755)

            # Run helper script with sudo
            success, message = run_with_sudo([script_path])

            if not success:
                raise Exception(message)

            messagebox.showinfo("Success",
                              "Configuration saved. The changes will take effect\n"
                              "after logging out and back in.")

        finally:
            # Cleanup temporary files
            try:
                shutil.rmtree(tmp_dir)
            except:
                pass

    def _apply_hyprland_configuration(self, config):
        """Apply Hyprland configuration"""
        user_config_dir = os.path.expanduser("~/.config/hypr")
        os.makedirs(user_config_dir, exist_ok=True)

        # Extract Hyprland config part
        hypr_config = config.split("monitor=")[1].split("# Add to /etc/modprobe.d")[0].strip()
        nvidia_config = "options nvidia " + config.split("options nvidia ")[1].strip()

        # Update Hyprland config
        config_path = os.path.join(user_config_dir, "custom-mode.conf")
        with open(config_path, 'w') as f:
            f.write("monitor=" + hypr_config)

        # Update NVIDIA config
        success, _ = run_with_sudo(['bash', '-c', f'echo "{nvidia_config}" > /etc/modprobe.d/nvidia.conf'])
        if not success:
            raise Exception("Failed to update NVIDIA configuration")

        messagebox.showinfo("Success",
                          "Configuration saved. The changes will take effect\n"
                          "after reloading Hyprland or restarting.")

def main():
    root = tk.Tk()
    app = LinuxCRU(root)
    root.mainloop()

if __name__ == "__main__":
    main()
