#!/bin/bash
# Hardware Integration Setup Script
# Run this with sudo to enable Webcam and Biometric features

echo "=== UnixSync Hardware Setup ==="

# 1. Continuity Camera (v4l2loopback)
echo "[1] Setting up Continuity Camera..."
if ! lsmod | grep v4l2loopback > /dev/null; then
    echo "    Installing v4l2loopback..."
    pacman -S --noconfirm v4l2loopback-dkms v4l2loopback-utils linux-headers
    modprobe v4l2loopback video_nr=10 card_label="iPhone Camera" exclusive_caps=1
    echo "    v4l2loopback module loaded on /dev/video10"
else
    echo "    v4l2loopback already loaded."
fi

# 2. Biometric Unlock (PAM)
echo "[2] Setting up Biometric Unlock..."
# Warning: Editing PAM is dangerous. We create a backup.
PAM_FILE="/etc/pam.d/sudo"
if [ -f "$PAM_FILE" ]; then
    cp "$PAM_FILE" "${PAM_FILE}.bak"
    echo "    Backed up sudo config to ${PAM_FILE}.bak"
    
    # We would insert 'auth sufficient pam_python.so src/integrations/pam_auth.py'
    # But since pam_python is rarely pre-installed, we just advise the user.
    echo "    [NOTE] To enable FaceID for sudo, install 'pam-python' (AUR) and add:"
    echo "           'auth sufficient pam_python.so /path/to/pam_auth.py'"
    echo "           to the top of $PAM_FILE"
fi

# 3. AirPlay Receiver
echo "[3] Setting up AirPlay Receiver..."
if ! command -v uxplay &> /dev/null; then
    echo "    Installing uxplay (AirPlay Mirroring)..."
    pacman -S --noconfirm uxplay
else
    echo "    uxplay already installed."
fi

echo "=== Setup Complete ==="
echo "You can now run 'uxplay' to mirror your iPhone screen!"
