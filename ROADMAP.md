# iCloud Sync Service for Manjaro - Project Roadmap

This document outlines the requirements, architecture, and features for a Linux-native iCloud synchronization service.

## 1. Core Requirements Checklist

### Authentication & Connection
- [ ] **iCloud Login Handler:** Securely handle Apple ID and Password.
- [ ] **2FA Management:** Support for Two-Factor Authentication (handling SMS/Device codes).
- [ ] **Session Persistence:** Store session cookies/tokens to avoid frequent re-logins.
- [ ] **Secure Storage:** Use Linux Keyring (via `keyring` library) to store credentials, avoiding plain text passwords.

### File Synchronization (iCloud Drive)
- [ ] **FUSE Filesystem (Virtual Files):** 
    - Implement a FUSE driver (using `fusepy` or `pyfuse3`).
    - Files appear in the directory structure but content is fetched on demand (streaming).
- [ ] **Local Cache:** Maintain a local cache for frequently accessed files to improve performance.
- [ ] **Two-Way Sync:**
    - Watch for local file changes (using `inotify` or `watchdog`).
    - Push changes to iCloud.
    - Poll or listen for remote changes from iCloud.
- [ ] **Directory Mapping:** Ability to map specific local folders (e.g., `~/Documents`, `~/Pictures`) to iCloud folders.

### Service Synchronization
- [ ] **Calendar:** Sync via standard CalDAV protocol.
- [ ] **Reminders:** Sync via CalDAV (Reminders are often exposed as tasks in CalDAV).
- [ ] **Contacts:** Sync via CardDAV protocol.
- [ ] **Notes:** Access via API (likely `pyicloud` web interface wrapper as Notes doesn't use standard IMAP anymore).

### Constraints & Logic
- [ ] **File Size Limits:**
    - Configurable threshold (e.g., "Auto-download files < 50MB").
    - Larger files appear as 0-byte placeholders or proprietary pointers until explicitly requested.
- [ ] **Selective Sync:** Configuration file to include/exclude specific paths.

## 2. Architecture Plan

**Tech Stack:**
- **Language:** Python (Strong library support for iCloud and FUSE).
- **Libraries:**
    - `pyicloud`: For general API access (Drive, Find My iPhone, basic services).
    - `fusepy`: For creating the virtual filesystem.
    - `watchdog`: For monitoring local file system events.
    - `caldav`: For Calendar/Reminders.
    - `keyring`: For credential storage.

**Components:**
1.  **`auth_manager.py`**: Handles login and session saving.
2.  **`fs_driver.py`**: The FUSE implementation. Intercepts file open/read calls.
3.  **`sync_daemon.py`**: Background process (Systemd service) monitoring for changes.
4.  **`config.yaml`**: User configuration (folders to sync, size limits).

## 4. Ecosystem Continuity (The "Apple Feel")
These features aim to replicate the "It just works" continuity between Apple devices, using Linux as a first-class citizen.

### "Handoff" & Universal Clipboard
*Since we cannot access the native Apple proprietary Handoff protocol, we emulate it via iCloud Drive + iOS Shortcuts.*

- [ ] **Web Handoff (iOS -> Linux):**
    - **Mechanism:** User runs an iOS Shortcut ("Open on Linux") -> Appends URL to `iCloud/LinuxSync/handoff_urls.txt`.
    - **Linux Action:** Daemon watches file -> Detects change -> Opens URL in default browser (Firefox/Chrome).
- [ ] **Universal Clipboard (Text):**
    - **Mechanism:** iOS Shortcut ("Copy to Linux") writes clipboard text to a hidden file `.clipboard`.
    - **Linux Action:** Daemon reads file -> Inject into Linux clipboard (using `xclip` or `wl-copy`).

### Device Awareness & Control ("Find My")
- [ ] **Battery Status:**
    - Show iPhone/iPad battery levels in the Manjaro System Tray (fetched via `pyicloud`).
- [ ] **"Ping Device":**
    - Right-click tray icon -> "Play Sound on iPhone" (useful for finding lost phone in the room).

### Remote Actions (Shortcuts Bridge)
- [ ] **Command Trigger Folder:**
    - Create a watched folder: `iCloud/LinuxSync/Commands/`.
    - **Example:** User saves a file named `lock_screen` to this folder from iPhone.
    - **Result:** Linux machine executes `loginctl lock-session`.
- [ ] **Photo Stream "Drop Zone":**
    - A special folder `~/iCloud/InstantDrop`.
    - Files placed here are immediately uploaded and a notification is sent to the iPhone (via a push notification service like Pushover, or just passively synced).

### Native Desktop Integration (Manjaro/KDE/GNOME)
- [ ] **KRunner / GNOME Search Provider:**
    - Allow searching iCloud files directly from the Start Menu/Launcher without opening the file manager.
- [ ] **"Share" Menu Plugin:**
    - Add "Send to iCloud" to the native Linux right-click context menu.
- [ ] **Theme Sync:**
    - Detect if it's night time (or if a flag file changes) and switch Linux system theme (Dark/Light) to match iPhone preference.

## 5. Ultimate "Power User" Features
For maximum integration, we bypass polling limits and add utility layers.

### The "Instant" Local Bridge (Local Network API)
*Instead of waiting for iCloud Drive file sync (which can take seconds), we run a tiny web server on Linux accessible over LAN.*
- [ ] **Local REST API:** `http://<linux-ip>:8080/api/v1/...`
- [ ] **Zero-Latency Control:**
    - iOS Shortcut calls `POST /clipboard` -> Instantly pastes on Linux.
    - iOS Shortcut calls `POST /open` -> Instantly opens URL.
    - iOS Shortcut calls `POST /type` -> Uses Linux as a remote keyboard (send text input).

### Focus Mode Mirroring
- [ ] **DND Sync:**
    - **iOS -> Linux:** iOS Automation runs when Focus turns ON -> Calls Local API -> Linux activates "Do Not Disturb" (via `dunstctl` or KDE DBus).
    - **Linux -> iOS:** (Harder, requires Mac acting as relay or specific focus status file check).

### Data Preservation & Utilities
- [ ] **Safari Reading List Exporter:**
    - Periodically fetch Reading List items and save them to a local `ReadingList.html` bookmarks file or sync to Firefox Sync.
- [ ] **"Time Machine" Lite:**
    - Automated backup of critical Linux dotfiles (`~/.bashrc`, `~/.config/i3`, etc.) to `iCloud/LinuxBackups/` with versioning.
- [ ] **Mail Config Generator:**
    - Script to auto-generate `mutt`, `thunderbird`, or `kmail` configurations using your iCloud credentials (IMAP/SMTP).

## 6. Hardware Convergence ("Continuity" on Steroids)
These features bridge the physical hardware gap, effectively turning the iPhone into a peripheral for the Linux machine.

- [ ] **"Continuity Camera" (Webcam):**
    - **Mechanism:** iPhone runs a stream (via an app or WebRTC), Linux ingests it via `v4l2loopback` (virtual webcam device).
    - **Result:** Use your iPhone's high-quality camera as the webcam for Zoom/Teams on Linux.
- [ ] **Biometric Unlock (FaceID for sudo):**
    - **Mechanism:** Custom PAM module (`pam_python`). When `sudo` is called, it sends a Push Request to the iPhone.
    - **Action:** User taps "Approve" (protected by FaceID) on an iOS Shortcut/Notification.
    - **Result:** Linux terminal authenticates without typing a password.
- [ ] **AirPlay Receiver (Audio/Video):**
    - **Integration:** Integrate `shairport-sync` (Audio) or `uxplay` (Mirroring) services.
    - **Result:** Select "Manjaro Linux" as an output speaker or screen from the iPhone Control Center.

## 7. Intelligence & Automation
- [ ] **"Hey Siri" for Linux:**
    - **Mechanism:** iOS Shortcuts exposing SSH commands over the Local API.
    - **Examples:** "Hey Siri, update system" (runs `pacman -Syu`), "Hey Siri, launch Steam".
- [ ] **Local AI "Second Brain":**
    - **Mechanism:** A background job indexes text from iCloud Notes and Documents.
    - **Result:** A local LLM (like Llama/Mistral running on Linux) allows you to "Chat with your iCloud" (e.g., "Summarize the meeting notes from last Tuesday").
- [ ] **Health Dashboard:**
    - **Mechanism:** iOS Automation exports Health XML daily to iCloud Drive.
    - **Result:** Linux parses this and renders a Grafana/Streamlit dashboard for detailed health analytics unavailable on the phone.
