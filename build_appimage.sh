#!/bin/bash
# build_appimage.sh - Main script to build the AppImage for Arch Linux

# Ensure required packages are installed
echo "Checking/installing required packages..."
sudo pacman -S --needed python tk xorg-server-utils imagemagick

# First check for the Python script
echo "Checking for linux-cru.py..."
if [ ! -f "linux-cru.py" ]; then
    echo "Error: linux-cru.py not found!"
    exit 1
fi

# Create directory structure
mkdir -p linux_cru.AppDir/{usr/{bin,share/{applications,icons/hicolor/{16x16,32x32,48x48,64x64,128x128,256x256,512x512,scalable}/apps},lib/python3.12/site-packages},etc}

# Copy your script
cp linux-cru.py linux_cru.AppDir/usr/bin/linux_cru
chmod +x linux_cru.AppDir/usr/bin/linux_cru

# Create the desktop entry
cat > linux_cru.AppDir/linux_cru.desktop << 'EOF'
[Desktop Entry]
Name=Linux Custom Resolution Utility
Exec=linux_cru
Icon=linux_cru
Type=Application
Categories=Settings
Comment=Custom Resolution Utility for Linux
EOF

# Create symlinks required by AppImage
ln -sf usr/share/applications/linux_cru.desktop linux_cru.AppDir/linux_cru.desktop
cp linux_cru.AppDir/linux_cru.desktop linux_cru.AppDir/usr/share/applications/

# Create SVG icon
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
    magick convert -background none -size ${size}x${size} \
        linux_cru.AppDir/usr/share/icons/hicolor/scalable/apps/linux_cru.svg \
        linux_cru.AppDir/usr/share/icons/hicolor/${size}x${size}/apps/linux_cru.png
done

# Copy the main icon to root for AppImage
cp linux_cru.AppDir/usr/share/icons/hicolor/256x256/apps/linux_cru.png linux_cru.AppDir/linux_cru.png

# Create AppRun script
cat > linux_cru.AppDir/AppRun << 'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
export PATH="${HERE}/usr/bin:${PATH}"
export PYTHONPATH="${HERE}/usr/lib/python3.12/site-packages:${PYTHONPATH}"
export LD_LIBRARY_PATH="${HERE}/usr/lib:${LD_LIBRARY_PATH}"
export TCL_LIBRARY="${HERE}/usr/lib/tcl8.6"
export TK_LIBRARY="${HERE}/usr/lib/tk8.6"

# Execute the main application
exec "${HERE}/usr/bin/linux_cru" "$@"
EOF
chmod +x linux_cru.AppDir/AppRun

# Copy required system libraries with sudo
echo "Copying system libraries..."
sudo cp /usr/lib/libtk8.6.so linux_cru.AppDir/usr/lib/
sudo cp /usr/lib/libtcl8.6.so linux_cru.AppDir/usr/lib/
sudo cp -r /usr/lib/tk8.6 linux_cru.AppDir/usr/lib/
sudo cp -r /usr/lib/tcl8.6 linux_cru.AppDir/usr/lib/
sudo chown -R $USER:$USER linux_cru.AppDir/usr/lib/

# Copy Python standard library
echo "Copying Python standard library..."
sudo cp -r /usr/lib/python3.12/* linux_cru.AppDir/usr/lib/python3.12/
sudo chown -R $USER:$USER linux_cru.AppDir/usr/lib/python3.12/

# Download appimagetool if not already present
if [ ! -f "appimagetool-x86_64.AppImage" ]; then
    echo "Downloading appimagetool..."
    wget -c "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x appimagetool-x86_64.AppImage
fi

# Build the AppImage
echo "Building AppImage..."
ARCH=x86_64 ./appimagetool-x86_64.AppImage linux_cru.AppDir Linux_CRU-x86_64.AppImage

echo "AppImage created successfully: Linux_CRU-x86_64.AppImage"