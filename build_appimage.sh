#!/bin/bash
# Build the Linux CRU AppImage.
#
# Bundles the Python interpreter, the standard library, tkinter and the
# whole Tcl/Tk runtime, so the result runs on a machine with no Python
# installed. Shared libraries are resolved with ldd rather than guessed,
# and the Tcl/Tk script directories are located by asking the
# interpreter, because distributions disagree about where they live
# (/usr/lib/tcl8.6 on Arch, /usr/share/tcltk/tcl8.6 on Debian/Ubuntu).
#
# The build verifies itself at the end: the bundled interpreter has to
# import tkinter and the application package with the host's Python
# hidden from it.

set -euo pipefail

APPDIR=linux_cru.AppDir
PYTHON=${PYTHON:-python3}

# Core libraries that must come from the host, not from us. Bundling
# these is what makes an AppImage crash on a system whose glibc differs.
EXCLUDE_RE='^(libc|libm|libdl|libpthread|librt|libresolv|libutil|libgcc_s|libstdc\+\+|ld-linux.*)\.so'

log() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ -f linux-cru.py ] || die "linux-cru.py not found; run this from the repo root"
[ -d linux_cru ] || die "linux_cru/ package not found; run this from the repo root"
command -v "$PYTHON" >/dev/null || die "$PYTHON not found"

PYVER=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYBIN=$("$PYTHON" -c 'import sys; print(sys.executable)')
STDLIB=$("$PYTHON" -c 'import sysconfig; print(sysconfig.get_paths()["stdlib"])')

log "Python $PYVER ($PYBIN)"
log "Standard library: $STDLIB"
"$PYTHON" -c 'import tkinter' 2>/dev/null || die "this Python has no tkinter (install python3-tk / tk)"

rm -rf "$APPDIR"
mkdir -p "$APPDIR"/usr/{bin,lib} \
         "$APPDIR"/usr/share/applications \
         "$APPDIR"/usr/share/icons/hicolor/{16x16,32x32,48x48,64x64,128x128,256x256,512x512,scalable}/apps

# ---------------------------------------------------------------- libraries

copy_libs() {  # copy the shared libraries a binary needs
    local target="$1" lib base
    ldd "$target" 2>/dev/null | awk '/=> \//{print $3}' | while read -r lib; do
        [ -f "$lib" ] || continue
        base=$(basename "$lib")
        if echo "$base" | grep -Eq "$EXCLUDE_RE"; then continue; fi
        [ -e "$APPDIR/usr/lib/$base" ] && continue
        cp -L "$lib" "$APPDIR/usr/lib/$base"
    done
}

# ---------------------------------------------------------------- interpreter

log "Copying the interpreter and standard library"
cp -L "$PYBIN" "$APPDIR/usr/bin/python$PYVER"
copy_libs "$APPDIR/usr/bin/python$PYVER"

mkdir -p "$APPDIR/usr/lib/python$PYVER"
# Only the standard library is needed. Third-party packages from the
# build machine, build-time headers and the test suites are all skipped;
# site-packages alone is usually hundreds of megabytes.
tar -C "$STDLIB" \
    --exclude='site-packages' --exclude='dist-packages' \
    --exclude='config-*' --exclude='__pycache__' \
    --exclude='test' --exclude='tests' --exclude='idlelib' \
    --exclude='turtledemo' --exclude='ensurepip' --exclude='pydoc_data' \
    --exclude='lib2to3' --exclude='*.pyo' --exclude='*.pyc' \
    -cf - . | tar -C "$APPDIR/usr/lib/python$PYVER" -xf -

# Every compiled extension module drags in its own libraries.
if [ -d "$APPDIR/usr/lib/python$PYVER/lib-dynload" ]; then
    find "$APPDIR/usr/lib/python$PYVER/lib-dynload" -name '*.so' | while read -r so; do
        copy_libs "$so"
    done
fi

# ---------------------------------------------------------------- tcl/tk

log "Locating the Tcl/Tk runtime"
TCL_DIR=$("$PYTHON" - <<'PY'
import tkinter, sys
try:
    r = tkinter.Tk(useTk=0)          # no display needed
    print(r.tk.exprstring('$tcl_library'))
except Exception:
    sys.exit(1)
PY
) || TCL_DIR=""

if [ -z "$TCL_DIR" ]; then
    for d in /usr/share/tcltk/tcl8.6 /usr/lib/tcl8.6 /usr/lib64/tcl8.6 /usr/share/tcl8.6; do
        [ -f "$d/init.tcl" ] && { TCL_DIR="$d"; break; }
    done
fi
[ -n "$TCL_DIR" ] && [ -f "$TCL_DIR/init.tcl" ] || die "could not find init.tcl (Tcl runtime)"

# tk_library needs a display to query, so derive it from the Tcl path.
TK_DIR=""
for candidate in \
    "$(dirname "$TCL_DIR")/tk${TCL_DIR##*tcl}" \
    /usr/share/tcltk/tk8.6 /usr/lib/tk8.6 /usr/lib64/tk8.6 /usr/share/tk8.6; do
    [ -f "$candidate/tk.tcl" ] && { TK_DIR="$candidate"; break; }
done
[ -n "$TK_DIR" ] || die "could not find tk.tcl (Tk runtime)"

log "Tcl: $TCL_DIR"
log "Tk:  $TK_DIR"
cp -r "$TCL_DIR" "$APPDIR/usr/lib/$(basename "$TCL_DIR")"
cp -r "$TK_DIR" "$APPDIR/usr/lib/$(basename "$TK_DIR")"
TCL_NAME=$(basename "$TCL_DIR")
TK_NAME=$(basename "$TK_DIR")

# Tcl packages that live next to the main directory (itcl, thread, ...)
for extra in "$(dirname "$TCL_DIR")"/tcl8 "$(dirname "$TCL_DIR")"/tcl8.6/tcl8; do
    [ -d "$extra" ] && cp -r "$extra" "$APPDIR/usr/lib/" 2>/dev/null || true
done

# ---------------------------------------------------------------- application

log "Copying the application"
cp linux-cru.py "$APPDIR/usr/bin/linux_cru"
chmod +x "$APPDIR/usr/bin/linux_cru"
cp -r linux_cru "$APPDIR/usr/lib/python$PYVER/linux_cru"
rm -rf "$APPDIR/usr/lib/python$PYVER/linux_cru/__pycache__"

# ---------------------------------------------------------------- desktop entry

cat > "$APPDIR/usr/share/applications/linux_cru.desktop" <<'EOF'
[Desktop Entry]
Name=Linux Custom Resolution Utility
Exec=linux_cru
Icon=linux_cru
Type=Application
Categories=Settings;
Comment=Create custom display resolutions and refresh rates
Terminal=false
EOF
cp "$APPDIR/usr/share/applications/linux_cru.desktop" "$APPDIR/linux_cru.desktop"

# ---------------------------------------------------------------- icon

cat > "$APPDIR/usr/share/icons/hicolor/scalable/apps/linux_cru.svg" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="50" fill="#2B3440"/>
  <rect x="56" y="76" width="400" height="280" rx="20" fill="#3B4252" stroke="#81A1C1" stroke-width="16"/>
  <rect x="76" y="96" width="360" height="240" rx="10" fill="#4C566A"/>
  <g stroke="#88C0D0" stroke-width="4" opacity="0.6">
    <line x1="156" y1="96" x2="156" y2="336"/>
    <line x1="236" y1="96" x2="236" y2="336"/>
    <line x1="316" y1="96" x2="316" y2="336"/>
    <line x1="396" y1="96" x2="396" y2="336"/>
    <line x1="76" y1="156" x2="436" y2="156"/>
    <line x1="76" y1="216" x2="436" y2="216"/>
    <line x1="76" y1="276" x2="436" y2="276"/>
  </g>
  <path d="M206 356 L306 356 L336 436 L176 436" fill="#3B4252" stroke="#81A1C1" stroke-width="16" stroke-linejoin="round"/>
  <g transform="translate(256, 256) scale(0.8)">
    <path d="M50,-80 A90,90 0 1,1 -50,-80" fill="none" stroke="#8FBCBB" stroke-width="24" stroke-linecap="round"/>
    <path d="M50,-80 L70,-40 L20,-70" fill="#8FBCBB"/>
  </g>
</svg>
EOF

CONVERT=""
command -v magick >/dev/null && CONVERT="magick"
[ -z "$CONVERT" ] && command -v convert >/dev/null && CONVERT="convert"
if [ -n "$CONVERT" ]; then
    for size in 16 32 48 64 128 256 512; do
        $CONVERT -background none -size ${size}x${size} \
            "$APPDIR/usr/share/icons/hicolor/scalable/apps/linux_cru.svg" \
            "$APPDIR/usr/share/icons/hicolor/${size}x${size}/apps/linux_cru.png" 2>/dev/null || true
    done
    cp "$APPDIR/usr/share/icons/hicolor/256x256/apps/linux_cru.png" \
       "$APPDIR/linux_cru.png" 2>/dev/null || true
fi
# AppImages need an icon at the root; fall back to the SVG.
[ -f "$APPDIR/linux_cru.png" ] || \
    cp "$APPDIR/usr/share/icons/hicolor/scalable/apps/linux_cru.svg" "$APPDIR/linux_cru.svg"

# ---------------------------------------------------------------- AppRun

cat > "$APPDIR/AppRun" <<EOF
#!/bin/bash
HERE="\$(dirname "\$(readlink -f "\${0}")")"

# Keep the caller's values. This application runs system tools (xrandr,
# kscreen-doctor, systemctl, pkexec) and they must not inherit the
# bundle's library paths, or they will load our copies of libraries
# instead of their own.
export LINUX_CRU_SAVED_MARKER=1
[ -n "\${PYTHONHOME:-}" ] && export LINUX_CRU_SAVED_PYTHONHOME="\$PYTHONHOME"
[ -n "\${PYTHONPATH:-}" ] && export LINUX_CRU_SAVED_PYTHONPATH="\$PYTHONPATH"
[ -n "\${LD_LIBRARY_PATH:-}" ] && export LINUX_CRU_SAVED_LD_LIBRARY_PATH="\$LD_LIBRARY_PATH"
[ -n "\${TCL_LIBRARY:-}" ] && export LINUX_CRU_SAVED_TCL_LIBRARY="\$TCL_LIBRARY"
[ -n "\${TK_LIBRARY:-}" ] && export LINUX_CRU_SAVED_TK_LIBRARY="\$TK_LIBRARY"

export APPDIR="\${APPDIR:-\$HERE}"
export PATH="\${HERE}/usr/bin:\${PATH}"
export PYTHONHOME="\${HERE}/usr"
export PYTHONPATH="\${HERE}/usr/lib/python$PYVER:\${HERE}/usr/lib/python$PYVER/lib-dynload"
export LD_LIBRARY_PATH="\${HERE}/usr/lib:\${LD_LIBRARY_PATH:-}"
export TCL_LIBRARY="\${HERE}/usr/lib/$TCL_NAME"
export TK_LIBRARY="\${HERE}/usr/lib/$TK_NAME"
export TKPATH="\${HERE}/usr/lib/$TK_NAME"
exec "\${HERE}/usr/bin/python$PYVER" "\${HERE}/usr/bin/linux_cru" "\$@"
EOF
chmod +x "$APPDIR/AppRun"

# ---------------------------------------------------------------- self-test

log "Verifying the bundle"
[ -f "$APPDIR/usr/lib/$TCL_NAME/init.tcl" ] || die "init.tcl missing from the bundle"
[ -f "$APPDIR/usr/lib/$TK_NAME/tk.tcl" ] || die "tk.tcl missing from the bundle"

# Run the bundled interpreter with the host's Python environment cleared,
# so anything it still needs from outside shows up as a failure here
# rather than on a user's machine.
HERE="$(readlink -f "$APPDIR")"
env -i \
    HOME="$HOME" \
    PATH="$HERE/usr/bin:/usr/bin:/bin" \
    PYTHONHOME="$HERE/usr" \
    PYTHONPATH="$HERE/usr/lib/python$PYVER:$HERE/usr/lib/python$PYVER/lib-dynload" \
    LD_LIBRARY_PATH="$HERE/usr/lib" \
    TCL_LIBRARY="$HERE/usr/lib/$TCL_NAME" \
    TK_LIBRARY="$HERE/usr/lib/$TK_NAME" \
    "$HERE/usr/bin/python$PYVER" - <<'PY' || die "the bundled interpreter is incomplete"
import sys
import tkinter, _tkinter                     # the hard one: pulls in libtk/libtcl
import linux_cru
from linux_cru import detect, edid, override, persist, privileged, timings, wayland
print(f"    bundled python {sys.version.split()[0]}, "
      f"tk {_tkinter.TK_VERSION}, linux_cru {linux_cru.__version__}: ok")
PY

log "Bundle size: $(du -sh "$APPDIR" | cut -f1)"

# ---------------------------------------------------------------- package

if [ "${SKIP_APPIMAGE:-0}" = "1" ]; then
    log "SKIP_APPIMAGE=1, stopping after the AppDir"
    exit 0
fi

if [ ! -f appimagetool-x86_64.AppImage ]; then
    log "Downloading appimagetool"
    wget -q -c "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x appimagetool-x86_64.AppImage
fi

log "Building the AppImage"
ARCH=x86_64 ./appimagetool-x86_64.AppImage "$APPDIR" Linux_CRU-x86_64.AppImage

[ -f Linux_CRU-x86_64.AppImage ] || die "AppImage creation failed"
chmod +x Linux_CRU-x86_64.AppImage
log "Created Linux_CRU-x86_64.AppImage ($(du -h Linux_CRU-x86_64.AppImage | cut -f1))"
