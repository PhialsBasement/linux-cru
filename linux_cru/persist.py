"""Persistent EDID overrides.

Installs a patched EDID so the kernel loads it at every boot:

1. the EDID file goes to /usr/lib/firmware/edid/linux-cru-<connector>.bin
2. drm.edid_firmware=<connector>:edid/... is added to the kernel command
   line (GRUB, Limine, or systemd-boot)
3. the file is added to the initramfs, which is required whenever the
   GPU driver loads before the root filesystem is mounted

Everything this module writes is namespaced (file names, a marker
comment in the bootloader config) so uninstalling removes exactly what
was added and nothing else. The kernel command line entry is rebuilt
from the set of installed files on every install and uninstall, so
several displays can be overridden at once without the entries
fighting each other.
"""

import glob
import os
import re

FIRMWARE_DIR = "/usr/lib/firmware/edid"
FILE_PREFIX = "linux-cru-"
MARKER = "# linux-cru: managed line, removed automatically on uninstall"

MKINITCPIO_DROPIN = "/etc/mkinitcpio.conf.d/99-linux-cru.conf"
DRACUT_DROPIN = "/etc/dracut.conf.d/99-linux-cru.conf"
INITRAMFS_TOOLS_HOOK = "/etc/initramfs-tools/hooks/linux-cru"

GRUB_DEFAULT = "/etc/default/grub"
LIMINE_DEFAULT = "/etc/default/limine"
KERNEL_CMDLINE = "/etc/kernel/cmdline"


def firmware_path(connector):
    return f"{FIRMWARE_DIR}/{FILE_PREFIX}{connector}.bin"


def firmware_rel(connector):
    """Path as the kernel parameter wants it (relative to the firmware dir)."""
    return f"edid/{FILE_PREFIX}{connector}.bin"


def installed_connectors():
    """Connectors that currently have a linux-cru EDID installed."""
    out = []
    for path in sorted(glob.glob(f"{FIRMWARE_DIR}/{FILE_PREFIX}*.bin")):
        name = os.path.basename(path)[len(FILE_PREFIX):-len(".bin")]
        out.append(name)
    return out


def active_on_cmdline():
    """drm.edid_firmware value currently in effect, or ''."""
    try:
        with open("/proc/cmdline") as f:
            cmdline = f.read()
    except OSError:
        return ""
    m = re.search(r"drm\.edid_firmware=(\S+)", cmdline)
    return m.group(1) if m else ""


def detect_bootloader():
    """('grub'|'limine'|'systemd-boot-uki'|'systemd-boot'|'unknown', detail)."""
    if os.path.exists(LIMINE_DEFAULT):
        return "limine", LIMINE_DEFAULT
    if os.path.exists(GRUB_DEFAULT):
        return "grub", GRUB_DEFAULT
    if os.path.exists(KERNEL_CMDLINE):
        return "systemd-boot-uki", KERNEL_CMDLINE
    for d in ("/boot/loader/entries", "/efi/loader/entries"):
        if os.path.isdir(d):
            return "systemd-boot", d
    return "unknown", ""


def detect_initramfs():
    """('mkinitcpio'|'dracut'|'initramfs-tools'|'none', detail)."""
    if os.path.exists("/etc/mkinitcpio.conf"):
        return "mkinitcpio", MKINITCPIO_DROPIN
    if os.path.isdir("/etc/dracut.conf.d") or os.path.exists("/usr/bin/dracut"):
        return "dracut", DRACUT_DROPIN
    if os.path.isdir("/etc/initramfs-tools"):
        return "initramfs-tools", INITRAMFS_TOOLS_HOOK
    return "none", ""


def describe_plan(connector):
    """Human-readable list of what installing will change."""
    boot, boot_detail = detect_bootloader()
    initrd, initrd_detail = detect_initramfs()
    steps = [f"Install the EDID to {firmware_path(connector)}"]
    if boot == "unknown":
        steps.append("Kernel command line: no supported bootloader found, "
                     "you will have to add the parameter yourself")
    else:
        steps.append(f"Add drm.edid_firmware to the kernel command line "
                     f"({boot}, {boot_detail})")
    if initrd == "none":
        steps.append("Initramfs: no supported tool found, skipping")
    else:
        steps.append(f"Add the file to the initramfs ({initrd}) and rebuild it")
    return steps


# -- script generation ---------------------------------------------------------

def _param_helper():
    return f"""
FWDIR='{FIRMWARE_DIR}'
PREFIX='{FILE_PREFIX}'

# Build "drm.edid_firmware=CONN:edid/file.bin,CONN2:..." from installed files.
cru_param() {{
    local parts=""
    local f base conn
    for f in "$FWDIR/$PREFIX"*.bin; do
        [ -e "$f" ] || continue
        base=$(basename "$f")
        conn=${{base#$PREFIX}}
        conn=${{conn%.bin}}
        if [ -n "$parts" ]; then parts="$parts,"; fi
        parts="$parts$conn:edid/$base"
    done
    if [ -n "$parts" ]; then printf 'drm.edid_firmware=%s' "$parts"; fi
}}
"""


def _bootloader_update_snippet():
    """Shell that rewrites the managed cmdline entry, whatever the bootloader."""
    return f"""
MARKER='{MARKER}'

strip_managed_lines() {{  # $1 = file
    [ -f "$1" ] || return 0
    grep -v -F "$MARKER" "$1" | grep -v '^KERNEL_CMDLINE\\[default\\]+=" drm\\.edid_firmware=' \\
        | grep -v '^GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT drm\\.edid_firmware=' \\
        > "$1.linux-cru-tmp" && mv "$1.linux-cru-tmp" "$1"
}}

update_bootloader() {{
    local param
    param=$(cru_param)

    if [ -f '{LIMINE_DEFAULT}' ]; then
        cp -n '{LIMINE_DEFAULT}' '{LIMINE_DEFAULT}.linux-cru-backup' 2>/dev/null || true
        strip_managed_lines '{LIMINE_DEFAULT}'
        if [ -n "$param" ]; then
            printf '%s\\n%s\\n' "$MARKER" \\
                "KERNEL_CMDLINE[default]+=\\" $param\\"" >> '{LIMINE_DEFAULT}'
        fi
        command -v limine-update >/dev/null 2>&1 && limine-update
        echo "bootloader: limine updated"

    elif [ -f '{GRUB_DEFAULT}' ]; then
        cp -n '{GRUB_DEFAULT}' '{GRUB_DEFAULT}.linux-cru-backup' 2>/dev/null || true
        strip_managed_lines '{GRUB_DEFAULT}'
        if [ -n "$param" ]; then
            printf '%s\\n%s\\n' "$MARKER" \\
                "GRUB_CMDLINE_LINUX_DEFAULT=\\"\\$GRUB_CMDLINE_LINUX_DEFAULT $param\\"" \\
                >> '{GRUB_DEFAULT}'
        fi
        if command -v grub-mkconfig >/dev/null 2>&1; then
            grub-mkconfig -o /boot/grub/grub.cfg
        elif command -v grub2-mkconfig >/dev/null 2>&1; then
            grub2-mkconfig -o /boot/grub2/grub.cfg
        fi
        echo "bootloader: grub updated"

    elif [ -f '{KERNEL_CMDLINE}' ]; then
        cp -n '{KERNEL_CMDLINE}' '{KERNEL_CMDLINE}.linux-cru-backup' 2>/dev/null || true
        sed -i -E 's/ ?drm\\.edid_firmware=[^ ]*//g' '{KERNEL_CMDLINE}'
        if [ -n "$param" ]; then
            printf '%s' " $param" >> '{KERNEL_CMDLINE}'
        fi
        echo "bootloader: /etc/kernel/cmdline updated (rebuild your UKI)"

    else
        local d
        for d in /boot/loader/entries /efi/loader/entries; do
            [ -d "$d" ] || continue
            local entry
            for entry in "$d"/*.conf; do
                [ -e "$entry" ] || continue
                case "$entry" in *fallback*) continue;; esac
                cp -n "$entry" "$entry.linux-cru-backup" 2>/dev/null || true
                sed -i -E 's/ ?drm\\.edid_firmware=[^ ]*//g' "$entry"
                if [ -n "$param" ]; then
                    sed -i -E "s|^(options .*)$|\\\\1 $param|" "$entry"
                fi
            done
            echo "bootloader: systemd-boot entries updated"
            return 0
        done
        echo "WARNING: no supported bootloader configuration found." >&2
        if [ -n "$param" ]; then
            echo "Add this to your kernel command line yourself: $param" >&2
        fi
    fi
}}
"""


def _initramfs_snippet():
    return f"""
update_initramfs_config() {{
    if [ -f /etc/mkinitcpio.conf ]; then
        local files=""
        local f
        for f in "$FWDIR/$PREFIX"*.bin; do
            [ -e "$f" ] || continue
            files="$files $f"
        done
        if [ -n "$files" ]; then
            mkdir -p /etc/mkinitcpio.conf.d
            printf '# Managed by Linux CRU\\nFILES+=(%s)\\n' "$files" \\
                > '{MKINITCPIO_DROPIN}'
        else
            rm -f '{MKINITCPIO_DROPIN}'
        fi
        mkinitcpio -P
        echo "initramfs: mkinitcpio rebuilt"

    elif command -v dracut >/dev/null 2>&1; then
        local items=""
        local f
        for f in "$FWDIR/$PREFIX"*.bin; do
            [ -e "$f" ] || continue
            items="$items $f"
        done
        mkdir -p /etc/dracut.conf.d
        if [ -n "$items" ]; then
            printf '# Managed by Linux CRU\\ninstall_items+="%s "\\n' "$items" \\
                > '{DRACUT_DROPIN}'
        else
            rm -f '{DRACUT_DROPIN}'
        fi
        dracut --force --regenerate-all
        echo "initramfs: dracut rebuilt"

    elif command -v update-initramfs >/dev/null 2>&1; then
        if ls "$FWDIR/$PREFIX"*.bin >/dev/null 2>&1; then
            mkdir -p /etc/initramfs-tools/hooks
            cat > '{INITRAMFS_TOOLS_HOOK}' <<'HOOKEOF'
#!/bin/sh
# Managed by Linux CRU
[ "$1" = prereqs ] && {{ echo; exit 0; }}
. /usr/share/initramfs-tools/hook-functions
mkdir -p "${{DESTDIR}}/lib/firmware/edid"
for f in /usr/lib/firmware/edid/linux-cru-*.bin; do
    [ -e "$f" ] || continue
    cp "$f" "${{DESTDIR}}/lib/firmware/edid/"
done
HOOKEOF
            chmod 755 '{INITRAMFS_TOOLS_HOOK}'
        else
            rm -f '{INITRAMFS_TOOLS_HOOK}'
        fi
        update-initramfs -u -k all
        echo "initramfs: update-initramfs rebuilt"

    else
        echo "initramfs: no supported tool found, skipped" >&2
    fi
}}
"""


def build_install_script(connector, edid_path):
    """Root script: install the EDID and make it load at boot."""
    return f"""#!/bin/bash
# linux-cru: install a persistent EDID override for {connector}
set -e
{_param_helper()}
{_bootloader_update_snippet()}
{_initramfs_snippet()}

if [ ! -s '{edid_path}' ]; then
    echo "ERROR: EDID file '{edid_path}' is missing or empty" >&2
    exit 1
fi

install -d -m 755 "$FWDIR"
install -m 644 '{edid_path}' '{firmware_path(connector)}'
echo "installed {firmware_path(connector)}"

update_bootloader
update_initramfs_config

echo
echo "Done. The override takes effect after a reboot."
echo "To undo it, use Remove in Linux CRU."
"""


def build_uninstall_script(connector=None):
    """Root script: remove one connector's override, or all of them."""
    if connector:
        removal = f"rm -f '{firmware_path(connector)}'\n" \
                  f"echo \"removed {firmware_path(connector)}\""
    else:
        removal = f"rm -f \"$FWDIR/$PREFIX\"*.bin\necho \"removed all Linux CRU EDID files\""
    return f"""#!/bin/bash
# linux-cru: remove persistent EDID override(s)
set -e
{_param_helper()}
{_bootloader_update_snippet()}
{_initramfs_snippet()}

{removal}

update_bootloader
update_initramfs_config

echo
echo "Done. The change takes effect after a reboot."
"""
