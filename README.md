# Orchard 🍎

**Orchard** is a seamless bridge between the Apple iCloud ecosystem and your Linux desktop. It brings your iCloud Drive, Photos, Notes, and more directly into your Unix environment, treating them as native citizens of your filesystem.

## Features

*   **iCloud Drive FUSE Mount:** Access your iCloud files as if they were local. Reads and writes are cached and synced in the background.
*   **Notes & Reminders Sync:** 
    *   **Reminders:** View your lists as Markdown files (`[ ] Task`).
    *   **Notes:** Access your notes as plain text files. (Powered by custom CloudKit reverse-engineering).
*   **Smart Caching:** Files are cached locally for performance and offline access, with graceful eviction to save disk space.
*   **System Integration:**
    *   **Handoff:** Open URLs from your iPhone directly in your Linux browser.
    *   **Universal Clipboard:** Copy on your iPhone, paste on Linux (and vice versa).
    *   **Shortcuts Integration:** Trigger Linux commands (like "Lock Screen") from iOS Shortcuts.
*   **Hardware Bridges:** (Experimental) Integration for Continuity Camera and AirPlay.
*   **Metadata Search:** Fast, local indexing of your cloud files for instant search results.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/rvardiashvili/Orchard.git
    cd Orchard
    ```

2.  **Run the startup script:**
    ```bash
    ./run.sh
    ```
    This will set up the Python environment, install dependencies, and start the service.

3.  **Login:**
    Follow the prompts to log in with your Apple ID. 2FA is supported.

## Configuration

Configuration is stored in `config/settings.yaml`. You can customize:
*   **Sync Root:** Where iCloud Drive appears on your system (default: `~/iCloud`).
*   **Cache Location:** Where local copies are stored.
*   **Folder Mappings:** Map specific iCloud folders (like "Downloads") to local Linux directories for two-way syncing.

## Architecture

Orchard uses a modular design:
*   **Orchard Core (`src/vfs.py`):** A custom FUSE filesystem handling file operations.
*   **CloudKit Integrations (`src/integrations/`):** Custom modules (`apple_reminders.py`, `apple_notes.py`) that reverse-engineer the web API to fetch app data.
*   **API Bridge (`src/api_server.py`):** A local HTTP server that accepts commands from iOS Shortcuts.
*   **Watchers (`src/watchers.py`):** Monitors the filesystem for changes to trigger clipboard and handoff events.

## License

MIT License
