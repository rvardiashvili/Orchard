# Development Goals

## Completed
- [x] **Basic FUSE Mount**: Filesystem browsing and attribute retrieval.
- [x] **Action Queue**: Asynchronous task processing using SQLite `actions` table.
- [x] **Lazy Open/Read**: Prevent file managers from blocking UI on `open()`. Only block on `read()` if content is missing.
- [x] **Thumbnailer Blocking**: Prevent `ffmpeg`, `nautilus`, etc., from triggering downloads for previews.
- [x] **Atomic Uploads**: Use temporary directory and symlinks to upload files with correct names without modifying local cache paths.
- [x] **Upload Conflict Handling**: Robustly handle `412` errors by identifying and overwriting conflicting remote files.
- [x] **Metadata Sync**: Immediate DB update after upload to prevent "delete-and-reupload" loops.
- [x] **Action Deduplication**: Prevent `write()` from flooding the queue with redundant `update_content` tasks.
- [x] **Offline Mode**: Start without internet, auto-reconnect, and sync when online.
- [x] **Conflict Resolution**: "Local Wins" strategy for uploads and renames (delete remote conflict).
- [x] **Error Recovery**: Network error detection, pause/resume service, and exponential backoff.
- [x] **Partial Sync / Streaming**: Hybrid strategy (Full download < 32MB, Sparse Chunked > 32MB) for instant access to large files.
- [x] **Desktop Integration**:
    - Context Menus ("Make Available Offline", "Free Up Space") for Nautilus, Nemo, Dolphin, Thunar.
    - Custom Emblems (Local, Cloud, Partial, Modified) for visual status.
    - Nautilus Status Column.
- [x] **System & UI**:
    - System Tray Icon with status indicators (Online/Offline/Syncing).
    - Control Panel Application (GTK3) for status overview and manual conflict resolution.
    - Setup Wizard for easy first-time configuration.
    - Autostart integration (.desktop file).
- [x] **Performance**: Multi-threaded Sync Engine (IO threads + Metadata thread).
- [x] **App Polishing**: Custom icons, About dialog, and refined UI.

## In Progress
- [ ] **Shared Folders**: Support for iCloud shared directories.

## Future Roadmap

### Phase 1 (Core Ecosystem)
- [ ] **Notes**: Bi-directional sync using virtual markdown files (`.orchard-note`).
- [ ] **Reminders**: Structured task lists synced as virtual files.
- [ ] **Contacts**: Read-only integration (vCard export/sync).
- [ ] **Calendar**: Read-only integration (iCal events).

### Phase 2 (Extended Media)
- [ ] **Photos**: Metadata-only sync initially, with on-demand download.
- [ ] **Mail**: (Feasibility study required).

### Phase 3 (Advanced)
- [ ] **Selective Sync**: Allow users to pin/unpin specific folders (Folder-level pinning).
- [ ] **Cross-Device Features**: Clipboard sync, AirDrop-style transfer (investigation needed).
- [ ] **Partial Sync**: Support range requests for large files (currently downloads full file).

## Future Roadmap

### Phase 1 (Core Ecosystem)
- [ ] **Notes**: Bi-directional sync using virtual markdown files (`.orchard-note`).
- [ ] **Reminders**: Structured task lists synced as virtual files.
- [ ] **Contacts**: Read-only integration (vCard export/sync).
- [ ] **Calendar**: Read-only integration (iCal events).

### Phase 2 (Extended Media)
- [ ] **Photos**: Metadata-only sync initially, with on-demand download.
- [ ] **Mail**: (Feasibility study required).

### Phase 3 (System & UI)
- [ ] **GUI Tray Icon**: Status indicator and simple controls.
- [ ] **Selective Sync**: Allow users to pin/unpin specific folders.
- [ ] **Shared Folders**: Support for iCloud shared directories.
- [ ] **Cross-Device Features**: Clipboard sync, AirDrop-style transfer (investigation needed).
