"""Runtime EDID override via DRM debugfs.

Writes a patched EDID to /sys/kernel/debug/dri/<minor>/<connector>/
edid_override and triggers a connector re-probe, so every compositor
immediately re-reads the mode list. Root is required; everything runs
through one self-contained shell script so a GUI can execute it with a
single pkexec call.

Safety model: the script arms a detached root-side watchdog that
reverts the override after a timeout unless a keep-flag file is
created. The revert does not depend on the calling process, the GUI,
or the user's session surviving.
"""

import os
import re

KEEP_FLAG_DIR = "/run"
DEFAULT_HOLD_SECONDS = 45


def drm_minor(card):
    """DRM minor number for a card name like 'card1'."""
    m = re.fullmatch(r"card(\d+)", card)
    if not m:
        raise ValueError(f"not a DRM card name: {card!r}")
    return int(m.group(1))


def debugfs_path(card, connector):
    return f"/sys/kernel/debug/dri/{drm_minor(card)}/{connector}"


def keep_flag_path(connector):
    return f"{KEEP_FLAG_DIR}/linux-cru-keep-{connector}"


_TEST_SCRIPT_TEMPLATE = """#!/bin/bash
# linux-cru: runtime EDID override test for @CONNECTOR@
set -u
DBG='@DBG@'
SYSFS='@SYSFS@'
EDID='@EDID@'
KEEP='@KEEP@'
HOLD=@HOLD@

if [ ! -e "$DBG/edid_override" ]; then
    echo "ERROR: $DBG/edid_override not available (kernel lockdown or debugfs unmounted)" >&2
    exit 1
fi
if [ ! -s "$EDID" ]; then
    echo "ERROR: EDID file $EDID missing or empty" >&2
    exit 1
fi
rm -f "$KEEP"

reprobe() {
    if [ -e "$DBG/trigger_hotplug" ]; then
        echo 1 > "$DBG/trigger_hotplug"
    else
        echo detect > "$SYSFS/status"
    fi
}

if ! cat "$EDID" > "$DBG/edid_override"; then
    echo "ERROR: kernel rejected the EDID override (bad checksum?)" >&2
    exit 1
fi
reprobe
echo "override active on @CONNECTOR@"

# Dead-man revert. Note: the "reset" write must NOT have a trailing
# newline (the kernel rejects it with EINVAL), hence printf.
WD=$(mktemp /tmp/linux-cru-watchdog-XXXXXX.sh)
cat > "$WD" <<WDEOF
#!/bin/bash
for i in \\$(seq $HOLD); do
    sleep 1
    if [ -f "$KEEP" ]; then
        rm -f "$KEEP" "\\$0"
        exit 0
    fi
done
printf reset > "$DBG/edid_override"
if [ -e "$DBG/trigger_hotplug" ]; then
    echo 1 > "$DBG/trigger_hotplug"
else
    echo detect > "$SYSFS/status"
fi
rm -f "\\$0"
WDEOF
chmod 700 "$WD"

# The watchdog must outlive this script, the calling process, the GUI,
# and the user session: run it as a systemd transient unit (owned by
# PID 1). Fall back to setsid (new session escapes process-group kill).
if command -v systemd-run >/dev/null 2>&1; then
    systemd-run --collect --quiet --unit "linux-cru-watchdog-@CONNECTOR@-$$" bash "$WD" \\
        || { setsid bash "$WD" >/dev/null 2>&1 < /dev/null & }
else
    setsid bash "$WD" >/dev/null 2>&1 < /dev/null &
fi
echo "auto-revert armed: $HOLD seconds (keep with: touch $KEEP)"
"""


def build_test_script(card, connector, edid_path,
                      hold_seconds=DEFAULT_HOLD_SECONDS):
    """Shell script: apply override, re-probe, arm dead-man revert.

    Run as root. Reverts automatically after `hold_seconds` unless
    the keep flag file is created (see keep_flag_path).
    """
    return (_TEST_SCRIPT_TEMPLATE
            .replace("@DBG@", debugfs_path(card, connector))
            .replace("@SYSFS@", f"/sys/class/drm/{card}-{connector}")
            .replace("@EDID@", str(edid_path))
            .replace("@KEEP@", keep_flag_path(connector))
            .replace("@HOLD@", str(int(hold_seconds)))
            .replace("@CONNECTOR@", connector))


def build_keep_script(connector):
    """Shell script (root): keep the active override, disarm the watchdog."""
    return f"#!/bin/bash\ntouch '{keep_flag_path(connector)}'\n"


def build_revert_script(card, connector):
    """Shell script (root): revert the override immediately."""
    dbg = debugfs_path(card, connector)
    sysfs = f"/sys/class/drm/{card}-{connector}"
    return f"""#!/bin/bash
set -u
# printf: the kernel rejects "reset" with a trailing newline
printf reset > '{dbg}/edid_override'
if [ -e '{dbg}/trigger_hotplug' ]; then
    echo 1 > '{dbg}/trigger_hotplug'
else
    echo detect > '{sysfs}/status'
fi
echo "override reverted on {connector}"
"""


def debugfs_available():
    """True if DRM debugfs looks usable (as root it still may be)."""
    return os.path.isdir("/sys/kernel/debug/dri") or os.geteuid() != 0
