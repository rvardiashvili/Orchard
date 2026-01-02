# Orchard 🍎

> **Native iCloud integration for Linux. No web browsers, no hacky scripts—just your filesystem.**

Orchard is a robust FUSE-based synchronization engine that creates a seamless bridge between your Linux workstation and the Apple ecosystem. It mounts your iCloud Drive, Notes, and Reminders as local files, treating your Linux machine as a first-class citizen in your device list.

---

## 📚 Documentation

*   **[Why Orchard?](MOTIVATION.md)** - Read about the philosophy behind the project and why "download scripts" aren't enough.
*   **[Technical Architecture](ARCHITECTURE.md)** - Deep dive into the Database, Sync Engine, and FUSE implementation details.
*   **[Development Roadmap](GOALS.md)** - Check our progress and see what features (Notes, Reminders, Photos) are coming next.

---

## ✨ Key Features

-   **System Tray & Control Panel**: Monitor sync status, view errors, and resolve file conflicts via a modern GUI dashboard.
-   **Partial Sync & Streaming**: Open large files (movies, archives) instantly. Orchard downloads only the required 8MB chunks on-demand.
-   **Desktop Integration**: 
    -   **Context Menus**: "Make Available Offline" and "Free Up Space" in Nautilus, Nemo, Dolphin, and Thunar.
    -   **Visual Status**: Custom emblems (Green Check, Cloud, Syncing) on files.
    -   **Native Experience**: Setup Wizard for easy configuration and Autostart support.
-   **Enhanced State Management**: Introduces `LocalState` and `CloudState` for explicit separation and management of local and cloud object metadata.
-   **Bi-directional Sync**: Changes made locally are pushed to iCloud, and remote changes are pulled down.
-   **Optimistic I/O**: Filesystem operations return *immediately*. The sync engine handles the network in the background with multi-threaded performance.
-   **Offline Mode**: Works seamlessly without internet. Browse cached files, make edits, and sync automatically when connectivity is restored.
-   **Robust Conflict Resolution**: "Local Wins" strategy protects your work. Manual conflict resolver tool available in Control Panel.

---

## 🚀 Usage

### Quick Start
Orchard provides an automated installer to set up dependencies, the virtual environment, and desktop integration.

```bash
# 1. Clone the repository
git clone https://github.com/rvardiashvili/orchard.git
cd orchard

# 2. Run the Installer
./install.sh

# 3. Start Orchard
# Launch "Orchard" from your applications menu, or run:
./src/main.py
```

### Manual Setup
If you prefer manual control:
```bash
# Setup environment (Use --system-site-packages for GUI support)
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt

# Install Desktop Extensions (Menus & Icons)
python tools/install_extensions.py

# Start the engine (GUI + FUSE)
python src/main.py
```

---

## ⚠️ Disclaimer

This project is based on reverse-engineering Apple's private APIs. It is not affiliated with, supported by, or endorsed by Apple Inc. Use at your own risk. Always back up your data.
