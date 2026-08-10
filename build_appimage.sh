#!/bin/bash
# build_appimage.sh - Fixed script to build the AppImage for Linux

# Get Python version
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_LIB_PATH="/usr/lib/python${PYTHON_VERSION}"

echo "Using Python ${PYTHON_VERSION}"
echo "Python lib path: ${PYTHON_LIB_PATH}"

# First check for the Python script
echo "Checking for linux-cru.py..."
if [ ! -f "linux-cru.py" ]; then
    echo "Error: linux-cru.py not found!"
    exit 1
fi

# Create directory structure
mkdir -p linux_cru.AppDir/usr/{bin,lib/python${PYTHON_VERSION},share/{applications,icons/hicolor/{16x16,32x32,48x48,64x64,128x128,256x256,512x512,scalable}/apps}}

# Copy your script and the linux_cru package (into the bundled Python's lib
# dir, which AppRun puts on PYTHONPATH)
cp linux-cru.py linux_cru.AppDir/usr/bin/linux_cru
chmod +x linux_cru.AppDir/usr/bin/linux_cru
cp -r linux_cru linux_cru.AppDir/usr/lib/python${PYTHON_VERSION}/linux_cru
rm -rf linux_cru.AppDir/usr/lib/python${PYTHON_VERSION}/linux_cru/__pycache__

# Create the desktop entry
cat > linux_cru.AppDir/usr/share/applications/linux_cru.desktop << 'EOF'
[Desktop Entry]
Name=Linux Custom Resolution Utility
Exec=linux_cru
Icon=linux_cru
Type=Application
Categories=Settings
Comment=Custom Resolution Utility for Linux
EOF

# Create desktop entry symlink
cp linux_cru.AppDir/usr/share/applications/linux_cru.desktop linux_cru.AppDir/

# Create SVG icon (same as original)
cat > linux_cru.AppDir/usr/share/icons/hicolor/scalable/apps/linux_cru.svg << 'EOF'
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

# Convert SVG to PNG for all required sizes
for size in 16 32 48 64 128 256 512; do
    convert -background none -size ${size}x${size} \
        linux_cru.AppDir/usr/share/icons/hicolor/scalable/apps/linux_cru.svg \
        linux_cru.AppDir/usr/share/icons/hicolor/${size}x${size}/apps/linux_cru.png
done

# Copy the main icon to root for AppImage
cp linux_cru.AppDir/usr/share/icons/hicolor/256x256/apps/linux_cru.png linux_cru.AppDir/linux_cru.png

# Create AppRun script with proper Python paths
cat > linux_cru.AppDir/AppRun << EOF
#!/bin/bash
HERE="\$(dirname "\$(readlink -f "\${0}")")"
export PATH="\${HERE}/usr/bin:\${PATH}"
export PYTHONPATH="\${HERE}/usr/lib/python${PYTHON_VERSION}:\${HERE}/usr/lib/python${PYTHON_VERSION}/lib-dynload:\${PYTHONPATH}"
export LD_LIBRARY_PATH="\${HERE}/usr/lib:\${LD_LIBRARY_PATH}"
export TCL_LIBRARY="\${HERE}/usr/lib/tcl8.6"
export TK_LIBRARY="\${HERE}/usr/lib/tk8.6"

# Execute the main application
exec "\${HERE}/usr/bin/python${PYTHON_VERSION}" "\${HERE}/usr/bin/linux_cru" "\$@"
EOF
chmod +x linux_cru.AppDir/AppRun

# Copy Python interpreter
cp /usr/bin/python${PYTHON_VERSION} linux_cru.AppDir/usr/bin/

# Copy essential Python standard library including tkinter
echo "Copying Python standard library..."
if [ -d "${PYTHON_LIB_PATH}" ]; then
    cp -r "${PYTHON_LIB_PATH}"/* linux_cru.AppDir/usr/lib/python${PYTHON_VERSION}/
else
    echo "Warning: Python library path ${PYTHON_LIB_PATH} not found"
fi

# Copy tkinter-specific libraries
echo "Copying tkinter libraries..."
# Find and copy tkinter dynamic libraries
find /usr/lib/x86_64-linux-gnu -name "*tk*" -o -name "*tcl*" | while read lib; do
    if [ -f "$lib" ]; then
        cp "$lib" linux_cru.AppDir/usr/lib/ 2>/dev/null || true
    fi
done

# Copy additional required libraries
libs_to_copy=(
    "libtk8.6.so*"
    "libtcl8.6.so*"
    "libX11.so*"
    "libXext.so*"
    "libXft.so*"
    "libfontconfig.so*"
    "libfreetype.so*"
    "libXrender.so*"
    "libexpat.so*"
    "libuuid.so*"
    "libpng16.so*"
    "libz.so*"
    "libbz2.so*"
    "liblzma.so*"
)

for lib_pattern in "${libs_to_copy[@]}"; do
    find /usr/lib/x86_64-linux-gnu /lib/x86_64-linux-gnu -name "$lib_pattern" 2>/dev/null | while read lib; do
        if [ -f "$lib" ]; then
            cp "$lib" linux_cru.AppDir/usr/lib/ 2>/dev/null || true
        fi
    done
done

# Copy tcl/tk library directories
if [ -d "/usr/lib/tcl8.6" ]; then
    cp -r /usr/lib/tcl8.6 linux_cru.AppDir/usr/lib/
fi
if [ -d "/usr/lib/tk8.6" ]; then
    cp -r /usr/lib/tk8.6 linux_cru.AppDir/usr/lib/
fi

# Copy any additional site-packages
if [ -d "/usr/lib/python${PYTHON_VERSION}/dist-packages" ]; then
    cp -r /usr/lib/python${PYTHON_VERSION}/dist-packages/* linux_cru.AppDir/usr/lib/python${PYTHON_VERSION}/ 2>/dev/null || true
fi

# Download appimagetool if not already present
if [ ! -f "appimagetool-x86_64.AppImage" ]; then
    echo "Downloading appimagetool..."
    wget -c "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x appimagetool-x86_64.AppImage
fi

# Build the AppImage
echo "Building AppImage..."
ARCH=x86_64 ./appimagetool-x86_64.AppImage linux_cru.AppDir Linux_CRU-x86_64.AppImage

if [ ! -f "Linux_CRU-x86_64.AppImage" ]; then
    echo "Error: AppImage creation failed!"
    exit 1
fi

chmod +x Linux_CRU-x86_64.AppImage
echo "AppImage created successfully: Linux_CRU-x86_64.AppImage"
echo "Size: $(du -h Linux_CRU-x86_64.AppImage | cut -f1)"
