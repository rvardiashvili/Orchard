#!/bin/bash
set -e

echo "🍏 Installing Orchard..."

# 1. System Dependencies
if command -v apt &> /dev/null; then
    echo "Detected apt. Installing system packages..."
    sudo apt update
    sudo apt install -y python3-venv python3-pip python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-appindicator3-0.1 libgirepository1.0-dev libcairo2-dev attr fuse3 python3-nautilus
elif command -v dnf &> /dev/null; then
    echo "Detected dnf. Installing system packages..."
    sudo dnf install -y python3-gobject gtk3 libappindicator-gtk3 attr fuse3 python3-nautilus
elif command -v pacman &> /dev/null; then
    echo "Detected pacman. Installing system packages..."
    sudo pacman -S --noconfirm python-gobject gtk3 libappindicator-gtk3 attr fuse3 python-nautilus
else
    echo "⚠️  Unsupported package manager. Please ensure Python3, Gtk3, AppIndicator3, Fuse3, and Attr are installed."
fi

# 2. Python Environment
echo "Setting up Python virtual environment..."
# Use --system-site-packages to access system Gi/Gtk
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Extensions & Icons
echo "Installing Desktop Extensions..."
python3 tools/install_extensions.py

# 4. Desktop Entry
echo "Creating Desktop Entry..."
REPO_DIR=$(pwd)
ICON_PATH="$HOME/.local/share/icons/hicolor/scalable/apps/orchard-logo.svg"
DESKTOP_FILE="$HOME/.local/share/applications/orchard.desktop"

mkdir -p "$HOME/.local/share/applications"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Name=Orchard
Comment=iCloud Drive for Linux
Exec=$REPO_DIR/.venv/bin/python $REPO_DIR/src/main.py
Icon=orchard-logo
Terminal=false
Type=Application
Categories=Network;FileTransfer;
StartupNotify=false
EOF

chmod +x "$DESKTOP_FILE"
echo "Desktop entry created at $DESKTOP_FILE"

echo "🍏 Installation Complete!"
echo "Run 'Orchard' from your application menu or execute: ./src/main.py"
