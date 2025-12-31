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

-   **Bi-directional Sync**: Changes made locally are pushed to iCloud, and remote changes are pulled down.
-   **Optimistic I/O**: Filesystem operations return *immediately*. The sync engine handles the network in the background.
-   **Smart Caching**: Files appear instantly, but content is downloaded only when you specifically read it.
-   **Thumbnail Blocking**: Intelligent filters prevent file managers from downloading your entire drive just to generate icons.
-   **Conflict Resolution**: Robust handling of version conflicts and network race conditions.

---

## 🚀 Usage

### Requirements
-   Python 3.11+
-   `fusepy`
-   `pyicloud`

### Quick Start
```bash
# Clone the repository
git clone https://github.com/rvardiashvili/orchard.git
cd orchard

# Setup environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Start the engine
python main.py
```

---

## ⚠️ Disclaimer

This project is based on reverse-engineering Apple's private APIs. It is not affiliated with, supported by, or endorsed by Apple Inc. Use at your own risk. Always back up your data.
