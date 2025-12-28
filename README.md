# Orchard 🍎

**Orchard** is a seamless bridge between the Apple iCloud ecosystem and your Linux desktop. It brings your iCloud Drive, Photos, Notes, and more directly into your Unix environment, treating them as native citizens of your filesystem.

## Features

*   **iCloud Drive FUSE Mount:** Access your iCloud files as if they were local. Reads and writes are cached and synced in the background.
*   **Service Sync:**
    *   **Reminders:** View your lists as Markdown files (`[ ] Task`).
    *   **Notes:** Access your notes as plain text files. (Powered by custom CloudKit reverse-engineering).
    *   **Calendar:** View events as Markdown summaries and standard `.ics` exports.
    *   **Contacts:** Auto-export contacts to standard `.vcf` format (with photos).
*   **Smart Caching:** Files are cached locally for performance and offline access, with graceful eviction to save disk space.

## Desktop Integration (Automatic Import)

To seamlessly integrate with Linux desktop applications:

*   **Calendar:** 
    1. Open your Calendar app (GNOME Calendar, Thunderbird, KOrganizer).
    2. Add a new "Network Calendar" or "Calendar from the Web".
    3. Enter the URL: `http://localhost:8080/api/v1/calendar.ics`
    4. Set refresh rate to 30 minutes.

*   **Contacts:**
    *   Download or link to `http://localhost:8080/api/v1/contacts.vcf`.
    *   (Most Linux Contact apps do not support live subscription to a VCF file yet, but you can script a periodic import).

*   **Desktop Integration (Automatic Import):** Provides endpoints for CalDAV/CardDAV subscription.
*   **Web UI (Orchard Apps):** A local web interface (`http://localhost:8080/apps`) for interactive management of:
    *   **Reminders:** View and toggle completion status directly.
    *   **Notes:** View notes content.

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
