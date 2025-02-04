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

class SudoPrompt:
    def __init__(self, parent):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Authentication Required")
        self.dialog.geometry("300x150")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        # Center the dialog on parent
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
        
        self.displays = self.get_displays()
        self.display_var = tk.StringVar(value=self.displays[0] if self.displays else "")
        
        display_combo = ttk.Combobox(display_frame, 
                                   textvariable=self.display_var,
                                   values=self.displays,
                                   state="readonly")
        display_combo.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
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

    def get_displays(self):
        """Get list of connected displays"""
        try:
            # Use xrandr -q instead of --listmonitors for more reliable output
            output = subprocess.check_output(['xrandr', '-q'],
                                          universal_newlines=True,
                                          stderr=subprocess.PIPE)
            
            # Find all connected displays
            # Match lines like "HDMI-0 connected" but not when prefixed with +
            displays = []
            for line in output.splitlines():
                if ' connected ' in line and not line.startswith('+'):
                    display = line.split()[0]
                    if display not in displays:  # Avoid duplicates
                        displays.append(display)
            
            return displays if displays else ["HDMI-0"]
        except subprocess.CalledProcessError:
            return ["HDMI-0"]

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
        """Generate configuration preview"""
        try:
            mode_name = f"{self.width_var.get()}x{self.height_var.get()}_{self.refresh_var.get()}"
            modeline = self.calculate_modeline()
            
            if not modeline:
                raise ValueError("Failed to calculate modeline parameters")
            
            force_options = """
    Option "ModeValidation" "AllowNonEdidModes,NoMaxPClkCheck,NoEdidMaxPClkCheck,NoMaxSizeCheck,NoHorizSyncCheck,NoVertRefreshCheck"
    Option "IgnoreEDID" "True\"""" if self.force_enable.get() else ""
            
            config = f"""# Generated by Linux CRU on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

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
            self.preview_text.delete(1.0, tk.END)
            self.preview_text.insert(1.0, config)
            
            # Update status
            self.status_var.set("Configuration generated successfully. Click 'Apply Configuration' to use these settings.")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to generate configuration: {str(e)}")
            self.status_var.set("Error: Failed to generate configuration")

    def apply_configuration(self):
        """Apply the configuration to the system using graphical sudo"""
        try:
            config = self.preview_text.get(1.0, tk.END)
            xorg_config = config.split("# Kernel module configuration")[0].strip()
            nvidia_config = "options nvidia " + config.split("options nvidia ")[1].strip()
            
            # Create temporary directory with random suffix for safety
            tmp_dir = f"/tmp/linux_cru_{os.getpid()}"
            os.makedirs(tmp_dir, exist_ok=True)
            
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
            
            # Cleanup temporary files
            try:
                shutil.rmtree(tmp_dir)
            except:
                pass
            
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
            
        except Exception as e:
            messagebox.showerror("Error",
                               f"Failed to apply configuration:\n{str(e)}\n\n"
                               "Make sure you have administrator privileges and "
                               "the required dependencies are installed.")
            self.status_var.set("Error: Failed to apply configuration")

def main():
    root = tk.Tk()
    app = LinuxCRU(root)
    root.mainloop()

if __name__ == "__main__":
    main()