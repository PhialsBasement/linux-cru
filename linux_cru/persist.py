"""Persistent EDID overrides.

The patched EDID always goes to
/usr/lib/firmware/edid/linux-cru-<connector>.bin. There are two ways to
make the kernel load it at boot:

"systemd" (the default)
    A oneshot service writes the override into the drm module's
    edid_firmware parameter and re-probes the connector. The kernel
    re-reads that parameter on every connector detect, so this is all
    it takes. The unit is ordered before the display manager, so the
    desktop still enumerates the display only once, with the patched
    mode list already in place. Nothing in the boot path is touched, so
    the worst case is a service that fails and a display that behaves
    exactly as it did before.

"cmdline" (for the boot console and the login screen)
    drm.edid_firmware=... is added to the kernel command line and the
    EDID file is added to the initramfs (required whenever the GPU
    driver loads before the root filesystem is mounted). This is the
    only way to have the mode present at the very first probe, before
    userspace exists, but a bad EDID here means recovering through the
    bootloader menu.

Everything written is namespaced (file names, a marker comment in the
bootloader config) so uninstalling removes exactly what was added. The
override list is rebuilt from the set of installed files on every
install and uninstall, so several displays can be overridden at once
without their entries conflicting.
"""

import glob
import os
import re

FIRMWARE_DIR = "/usr/lib/firmware/edid"
FILE_PREFIX = "linux-cru-"
MARKER = "# linux-cru: managed line, removed automatically on uninstall"

MKINITCPIO_CONF = "/etc/mkinitcpio.conf"
MKINITCPIO_DROPIN = "/etc/mkinitcpio.conf.d/99-linux-cru.conf"
DRACUT_DROPIN = "/etc/dracut.conf.d/99-linux-cru.conf"
INITRAMFS_TOOLS_HOOK = "/etc/initramfs-tools/hooks/linux-cru"

GRUB_DEFAULT = "/etc/default/grub"
LIMINE_DEFAULT = "/etc/default/limine"
KERNEL_CMDLINE = "/etc/kernel/cmdline"

SYSTEMD_UNIT = "/etc/systemd/system/linux-cru-edid.service"
SYSTEMD_HELPER = "/usr/lib/linux-cru/apply-edid.sh"

METHOD_SYSTEMD = "systemd"
METHOD_CMDLINE = "cmdline"


def firmware_path(connector):
    return f"{FIRMWARE_DIR}/{FILE_PREFIX}{connector}.bin"


def systemd_unit_text():
    """The exact contents of the boot service that gets installed."""
    return (f"[Unit]\n"
            f"Description=Linux CRU custom display modes\n"
            f"Documentation=https://github.com/PhialsBasement/linux-cru\n"
            f"After=local-fs.target systemd-modules-load.service\n"
            f"Before=display-manager.service graphical.target\n"
            f"ConditionPathExists={SYSTEMD_HELPER}\n\n"
            f"[Service]\n"
            f"Type=oneshot\n"
            f"RemainAfterExit=yes\n"
            f"ExecStart={SYSTEMD_HELPER}\n\n"
            f"[Install]\n"
            f"WantedBy=graphical.target\n")


def systemd_helper_text():
    """The helper the boot service runs: writes edid_firmware, reprobes."""
    return (f"#!/bin/bash\n"
            f"# Applies the installed EDID overrides, then re-probes the\n"
            f"# connectors so the new mode lists are in place before the\n"
            f"# desktop starts.\n"
            f"value=\"\"\n"
            f"for f in {FIRMWARE_DIR}/{FILE_PREFIX}*.bin; do\n"
            f"    [ -e \"$f\" ] || continue\n"
            f"    conn=$(basename \"$f\"); conn=${{conn#{FILE_PREFIX}}}; conn=${{conn%.bin}}\n"
            f"    [ -n \"$value\" ] && value=\"$value,\"\n"
            f"    value=\"$value$conn:edid/$(basename \"$f\")\"\n"
            f"done\n"
            f"echo \"$value\" > /sys/module/drm/parameters/edid_firmware\n"
            f"# ...then trigger_hotplug / status=detect on each connector\n")


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
    if os.path.exists(MKINITCPIO_CONF):
        return "mkinitcpio", MKINITCPIO_DROPIN
    if os.path.isdir("/etc/dracut.conf.d") or os.path.exists("/usr/bin/dracut"):
        return "dracut", DRACUT_DROPIN
    if os.path.isdir("/etc/initramfs-tools"):
        return "initramfs-tools", INITRAMFS_TOOLS_HOOK
    return "none", ""


def installed_method():
    """Which persistence method is currently installed, or None."""
    if os.path.exists(SYSTEMD_UNIT):
        return METHOD_SYSTEMD
    for path in (LIMINE_DEFAULT, GRUB_DEFAULT):
        try:
            with open(path) as f:
                if MARKER in f.read():
                    return METHOD_CMDLINE
        except OSError:
            pass
    try:
        with open(KERNEL_CMDLINE) as f:
            if f"edid/{FILE_PREFIX}" in f.read():
                return METHOD_CMDLINE
    except OSError:
        pass
    return None


def describe_plan(connector, method=METHOD_SYSTEMD):
    """Human-readable list of what installing will change."""
    steps = [f"Install the EDID to {firmware_path(connector)}"]
    if method == METHOD_SYSTEMD:
        steps.append(f"Install a boot service ({SYSTEMD_UNIT}) that adds the "
                     "mode to the display's list before the desktop starts")
        steps.append("Add the mode to the display's list now")
        steps.append("Your resolution is not changed -- the mode just becomes "
                     "available to select; nothing in the boot path is changed")
        return steps

    boot, boot_detail = detect_bootloader()
    initrd, _ = detect_initramfs()
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

# Build "CONN:edid/file.bin,CONN2:..." from the installed files. This is
# the value of the drm module's edid_firmware parameter; on the kernel
# command line it is prefixed with "drm.edid_firmware=".
cru_value() {{
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
    printf '%s' "$parts"
}}

cru_param() {{
    local v
    v=$(cru_value)
    if [ -n "$v" ]; then printf 'drm.edid_firmware=%s' "$v"; fi
}}

# Connectors we have EDIDs installed for.
cru_connectors() {{
    local f base conn
    for f in "$FWDIR/$PREFIX"*.bin; do
        [ -e "$f" ] || continue
        base=$(basename "$f")
        conn=${{base#$PREFIX}}
        printf '%s\\n' "${{conn%.bin}}"
    done
}}
"""


def _systemd_snippet():
    """Shell that regenerates (or removes) the boot-time service."""
    return f"""
UNIT='{SYSTEMD_UNIT}'
HELPER='{SYSTEMD_HELPER}'

write_helper() {{
    mkdir -p "$(dirname "$HELPER")"
    cat > "$HELPER" <<'HELPEREOF'
#!/bin/bash
# Managed by Linux CRU. Applies the EDID overrides for the installed
# displays, then re-probes them so the new mode lists are in place
# before the desktop starts.
set -u
FWDIR='@FWDIR@'
PREFIX='@PREFIX@'

value=""
for f in "$FWDIR/$PREFIX"*.bin; do
    [ -e "$f" ] || continue
    base=$(basename "$f")
    conn=${{base#$PREFIX}}
    conn=${{conn%.bin}}
    [ -n "$value" ] && value="$value,"
    value="$value$conn:edid/$base"
done
[ -n "$value" ] || exit 0

echo "$value" > /sys/module/drm/parameters/edid_firmware

# Re-probe: the driver already detected these connectors (with the
# original EDID) before the root filesystem was mounted.
for f in "$FWDIR/$PREFIX"*.bin; do
    [ -e "$f" ] || continue
    base=$(basename "$f")
    conn=${{base#$PREFIX}}
    conn=${{conn%.bin}}
    for path in /sys/class/drm/card*-"$conn"; do
        [ -e "$path/status" ] || continue
        card=$(basename "$path"); card=${{card%%-*}}
        minor=$(cut -d: -f2 "/sys/class/drm/$card/dev" 2>/dev/null)
        if [ -n "$minor" ] && [ -e "/sys/kernel/debug/dri/$minor/$conn/trigger_hotplug" ]; then
            echo 1 > "/sys/kernel/debug/dri/$minor/$conn/trigger_hotplug" || true
        else
            echo detect > "$path/status" || true
        fi
    done
done
HELPEREOF
    sed -i "s|@FWDIR@|$FWDIR|g; s|@PREFIX@|$PREFIX|g" "$HELPER"
    chmod 755 "$HELPER"
}}

update_systemd() {{
    if [ -z "$(cru_value)" ]; then
        if [ -f "$UNIT" ]; then
            systemctl disable --quiet linux-cru-edid.service 2>/dev/null || true
            rm -f "$UNIT"
            systemctl daemon-reload 2>/dev/null || true
        fi
        rm -f "$HELPER"
        rmdir "$(dirname "$HELPER")" 2>/dev/null || true
        echo "boot service: removed"
        return 0
    fi

    write_helper
    cat > "$UNIT" <<UNITEOF
[Unit]
Description=Linux CRU custom display modes
Documentation=https://github.com/PhialsBasement/linux-cru
After=local-fs.target systemd-modules-load.service
Before=display-manager.service graphical.target
ConditionPathExists=$HELPER

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$HELPER

[Install]
WantedBy=graphical.target
UNITEOF
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable --quiet linux-cru-edid.service 2>/dev/null || true
    echo "boot service: installed and enabled"
}}

apply_now() {{
    if [ -x "$HELPER" ]; then
        "$HELPER" && echo "override applied to the running system"
    fi
}}

clear_now() {{
    printf '\\n' > /sys/module/drm/parameters/edid_firmware 2>/dev/null || true
    local conn path card minor
    for path in /sys/class/drm/card*-*; do
        [ -e "$path/status" ] || continue
        case "$(basename "$path")" in *-Writeback-*) continue;; esac
        card=$(basename "$path"); conn=${{card#*-}}; card=${{card%%-*}}
        minor=$(cut -d: -f2 "/sys/class/drm/$card/dev" 2>/dev/null)
        if [ -n "$minor" ] && [ -e "/sys/kernel/debug/dri/$minor/$conn/trigger_hotplug" ]; then
            echo 1 > "/sys/kernel/debug/dri/$minor/$conn/trigger_hotplug" 2>/dev/null || true
        fi
    done
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
    if [ -f '{MKINITCPIO_CONF}' ]; then
        local files=""
        local f
        for f in "$FWDIR/$PREFIX"*.bin; do
            [ -e "$f" ] || continue
            files="$files $f"
        done
        if [ -n "$files" ]; then
            mkdir -p "$(dirname '{MKINITCPIO_DROPIN}')"
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
        mkdir -p "$(dirname '{DRACUT_DROPIN}')"
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
            mkdir -p "$(dirname '{INITRAMFS_TOOLS_HOOK}')"
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


def build_install_script(connector, edid_path, method=METHOD_SYSTEMD,
                         apply_now=True):
    """Root script: install the EDID and make it load at boot.

    method: METHOD_SYSTEMD (boot service, nothing in the boot path) or
    METHOD_CMDLINE (kernel command line plus initramfs).
    apply_now: also apply it to the running system straight away.
    """
    if method == METHOD_CMDLINE:
        steps = "update_bootloader\nupdate_initramfs_config"
        closing = ('echo "Done. After a reboot the mode is in the display\'s '
                   'list; select it in your display settings."')
    else:
        steps = "update_systemd" + ("\napply_now" if apply_now else "")
        closing = ('echo "Done. The mode is in the display\'s list now and is '
                   're-added at every boot. Your resolution is not changed -- '
                   'select the mode in your display settings."')
    return f"""#!/bin/bash
# linux-cru: install a persistent EDID override for {connector} ({method})
set -e
{_param_helper()}
{_systemd_snippet()}
{_bootloader_update_snippet()}
{_initramfs_snippet()}

if [ ! -s '{edid_path}' ]; then
    echo "ERROR: EDID file '{edid_path}' is missing or empty" >&2
    exit 1
fi

install -d -m 755 "$FWDIR"
install -m 644 '{edid_path}' '{firmware_path(connector)}'
echo "installed {firmware_path(connector)}"

{steps}

echo
{closing}
echo "To undo it, use Remove in Linux CRU."
"""


def build_uninstall_script(connector=None):
    """Root script: remove one connector's override, or all of them.

    Cleans up whichever method was used: the boot service is always
    regenerated from what is left, and the kernel command line and
    initramfs are only touched if this tool put something there.
    """
    if connector:
        removal = f"rm -f '{firmware_path(connector)}'\n" \
                  f"echo \"removed {firmware_path(connector)}\""
    else:
        removal = 'rm -f "$FWDIR/$PREFIX"*.bin\necho "removed all Linux CRU EDID files"'
    return f"""#!/bin/bash
# linux-cru: remove persistent EDID override(s)
set -e
{_param_helper()}
{_systemd_snippet()}
{_bootloader_update_snippet()}
{_initramfs_snippet()}

{removal}

update_systemd

# Only touch the boot path if this tool changed it.
if grep -qsF "$MARKER" '{LIMINE_DEFAULT}' '{GRUB_DEFAULT}' 2>/dev/null \\
   || grep -qs 'edid/{FILE_PREFIX}' '{KERNEL_CMDLINE}' 2>/dev/null \\
   || ls /boot/loader/entries/*.linux-cru-backup >/dev/null 2>&1; then
    update_bootloader
fi
if [ -f '{MKINITCPIO_DROPIN}' ] || [ -f '{DRACUT_DROPIN}' ] \\
   || [ -f '{INITRAMFS_TOOLS_HOOK}' ]; then
    update_initramfs_config
fi

clear_now
echo
echo "Done."
"""
