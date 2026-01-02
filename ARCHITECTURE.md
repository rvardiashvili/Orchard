# Architecture & Technical Design

Orchard is a complex state-synchronization engine. This document details the internal components, database schema, and decision logic that powers the system.

## 1. Core Principles

1.  **OrchardDB is the Brain**: The filesystem is just a user-facing view. The SQLite database is the single source of truth for local state.
2.  **Eventual Consistency**: We accept that local state and cloud state will diverge. We use dirty flags (`sync_state`) to track reconciliation needs.
3.  **Optimistic UI**: Local operations are instant. Network operations are asynchronous.

## 2. Component Diagram

```text
┌────────────┐
│ Apple APIs │
└─────┬──────┘
      │ (Network)
┌─────▼──────────┐      ┌───────────────┐
│ iCloud Client  │◄────►│  Sync Engine  │
│ (pyicloud wrapper)    │ (Worker Loop) │
└────────────────┘      └───────┬───────┘
                                │
                        ┌───────▼───────┐
                        │   OrchardDB   │
                        │   (SQLite)    │
                        └───────▲───────┘
                                │
                        ┌───────┴───────┐
                        │   OrchardFS   │
                        │    (FUSE)     │
                        └───────▲───────┘
                                │
                        ┌───────▼───────┐
                        │  Linux Kernel │
                        │     (VFS)     │
                        └───────────────┘
```

## 3. Database Schema

The database (`db.sqlite`) tracks every object's metadata and synchronization status, acting as the single source of truth for the local system.

### `objects` Table
Stores the comprehensive state of every file or folder managed by Orchard. This table combines both local and known cloud metadata, allowing the `OrchardObject` to abstractly represent a file system entry.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | TEXT | Local unique UUID for the object. |
| `type` | TEXT | Object type: `'file'` or `'folder'`. |
| `parent_id` | TEXT | Local unique UUID of the parent folder. |
| `name` | TEXT | The object's local name (without extension). |
| `size` | INTEGER | The object's size in bytes. |
| `extension` | TEXT | The object's file extension. |
| `cloud_id` | TEXT | Apple's server-side ID (Document ID or Drive ID). |
| `cloud_parent_id` | TEXT | Apple's server-side ID of the parent folder. |
| `etag` | TEXT | Cloud HTTP ETag for versioning and change detection. |
| `missing_from_cloud` | INTEGER | Flag (0/1) indicating if the object is believed to be missing from the cloud. |
| `local_modified_at` | INTEGER | Unix timestamp of the last local modification. |
| `cloud_modified_at` | INTEGER | Unix timestamp of the last cloud modification. |
| `revision` | TEXT | Cloud revision ID, used for version tracking. |
| `origin` | TEXT | Indicates where the object was first created: `'local'` or `'cloud'`. |
| `sync_state` | TEXT | Current synchronization status: `synced`, `dirty`, `conflict`, `pending_push`, `pending_pull`, `error`. |
| `dirty` | INTEGER | Flag (0/1) indicating if the local object has un-synced changes. |
| `deleted` | INTEGER | Flag (0/1) indicating if the object is marked for deletion. |
| `last_synced` | INTEGER | Unix timestamp of the last successful synchronization. |

### `shadows` Table
This table stores a "shadow" copy of critical cloud metadata *before* a local change was applied or *after* a cloud change was observed. It is crucial for robust conflict detection and ensuring atomic updates by providing a baseline for comparison.

| Column | Type | Description |
| :--- | :--- | :--- |
| `object_id` | TEXT | Foreign key to the `objects` table. |
| `cloud_id` | TEXT | Cloud ID of the object at the time of shadow creation. |
| `parent_id` | TEXT | Local parent ID at the time of shadow creation. |
| `name` | TEXT | Name of the object at the time of shadow creation. |
| `etag` | TEXT | ETag of the object at the time of shadow creation. |
| `file_hash` | TEXT | Hash of the file content at the time of shadow creation (for files). |
| `modified_at` | INTEGER | Unix timestamp of when the shadow state was captured. |

### `drive_cache` Table
Specific cache metadata for iCloud Drive objects, managing local file presence and access.

| Column | Type | Description |
| :--- | :--- | :--- |
| `object_id` | TEXT | Foreign key to the `objects` table. |
| `local_path` | TEXT | The full path to the locally cached file/folder. |
| `size` | INTEGER | Size of the locally cached file. |
| `file_hash` | TEXT | SHA256 hash of the local file content. |
| `present_locally` | INTEGER | `0`: Missing (Cloud), `1`: Full, `2`: Partial (Sparse). |
| `pinned` | INTEGER | Flag (0/1) if the file is explicitly pinned for local retention. |
| `last_accessed` | INTEGER | Unix timestamp of the last local access. |
| `open_count` | INTEGER | Number of current open handles to the local file. |

### `chunk_cache` Table
Tracks downloaded 8MB chunks for large files (Sparse Caching).

| Column | Type | Description |
| :--- | :--- | :--- |
| `object_id` | TEXT | Foreign key to the `objects` table. |
| `chunk_index` | INTEGER | Index of the 8MB block (0, 1, 2...). |
| `last_accessed` | INTEGER | Timestamp for potential LRU eviction of chunks. |

### `actions` Table
A persistent, prioritized task queue for the Sync Engine. Actions can be coalesced and retried with exponential backoff.

## 4. Synchronization Logic

### Multi-Threaded Sync Engine
The Sync Engine (`src/sync/engine.py`) uses a `ThreadPoolExecutor` to handle IO-heavy tasks (`upload`, `download`, `download_chunk`) asynchronously on worker threads. Metadata tasks (`list_children`, `rename`) run on the main thread to ensure high priority and responsiveness.

### Partial Sync & Streaming (Sparse Caching)
For large files (> 32MB), Orchard uses a "Sparse Caching" strategy:
1.  **On Open**: A sparse placeholder is created (takes 0 disk space).
2.  **On Read**: If the requested byte range is missing, FUSE blocks and queues a `download_chunk` action.
3.  **Engine**: Downloads only the requested 8MB chunk and writes it to the sparse file.
4.  **Result**: Instant playback for movies/media without waiting for full download.

### Desktop Integration
Orchard integrates deeply with Linux desktop environments via:
*   **Extensions**: Python scripts (Nautilus/Nemo) and Action/Desktop files (Dolphin/Thunar) provide context menus ("Make Available Offline").
*   **Extended Attributes (`xattr`)**: `OrchardFS` exposes status via `user.orchard.status` and `user.xdg.emblems`.
*   **Custom Emblems**: SVG icons (`vcs-normal`, `vcs-branch`, etc.) installed to `hicolor` theme provide visual status indicators (Local, Cloud, Partial, Modified) across all file managers.

### GUI Architecture
*   **Tray Icon**: `src/gui/tray.py` runs a GTK3 AppIndicator loop in the main thread. It polls the Sync Engine for status.
*   **Control Panel**: `src/gui/window.py` provides a dashboard for conflict resolution and settings.
*   **Setup Wizard**: `src/gui/wizard.py` handles first-run configuration (Apple ID, Mount Point) and authentication.
*   **Process Model**: Single process. `Main Thread` runs GUI. `Daemon Threads` run FUSE and Sync Engine.

### Configuration & Deployment
*   **Config Manager**: `src/config/manager.py` handles persistent settings in `~/.config/orchard/config.json`.
*   **Installation**: `install.sh` automates dependency installation, virtual environment creation, and desktop shortcut generation (`~/.local/share/applications/orchard.desktop`).

### The "Lazy Open" Strategy
To prevent file managers (Nautilus, Dolphin) from freezing the UI:
1.  `open()` returns immediately.

### Action Queueing and Coalescing
The `actions` table functions as a persistent, prioritized task queue. The Sync Engine optimizes operations by:
*   **Prioritization**: Actions triggered by immediate user interaction (e.g., file opens) can be given higher priority.
*   **Coalescing**: Sequential, redundant actions on the same object are merged. For example, multiple renames of the same file before it's synced will result in a single `rename` action with the final target name. This prevents unnecessary network calls.
*   **Retry with Exponential Backoff**: Failed actions are automatically retried with an increasing delay, preventing network congestion and gracefully handling transient errors.

### Sync Engine Loop & Connectivity
The Sync Engine runs an infinite loop that:
1.  **Checks Connectivity**: Before processing tasks, it verifies internet access and authentication. If offline, it pauses and retries periodically.
2.  **Processes Queue**: It fetches the next pending `action` from the database.
3.  **Handles Errors**: Network errors trigger a pause and backoff. Fatal errors mark the action as failed.

### Robust Conflict Resolution (Local Wins)
Orchard enforces a "Local Wins" strategy for file conflicts to prioritize user data:
1.  **Uploads**: Before uploading a new file, the engine checks if a file with the same name already exists on the cloud. If so, it **deletes the remote file** before uploading the local version.
2.  **Renames**: Similar to uploads, if a rename target exists remotely, the remote item is deleted first.
3.  **Concurrency**: The system uses `shadows` and ETags to detect if the cloud state has drifted, but for direct naming collisions, the local intent overrides the remote state.

### Atomic Writes
1.  **Uploads**: We create a symlink in a temporary directory to ensure the upload has the correct filename without moving the actual cache file.
2.  **Downloads**: We download to `filename.part` and use `os.rename` to atomically swap it into place, ensuring no partial reads.

## 5. Object Model

Orchard employs a robust object model to represent various iCloud entities (files, folders, notes, reminders) in a unified way. The core of this model is the `OrchardObject` base class, which utilizes a composition pattern to separate local and cloud-specific metadata.

*   **`OrchardObject`**: The foundational class for all synchronized entities. It composes two key state objects:
    *   **`LocalState`**: Encapsulates metadata pertaining to the object's local representation and status within the Orchard system (e.g., `name`, `extension`, `parent_id`, `dirty` flag, `sync_state`).
    *   **`CloudState`**: Encapsulates metadata pertaining to the object's representation in iCloud (e.g., `cloud_id`, `cloud_parent_id`, `etag`, `revision`).
    This separation allows for clearer logic in the Sync Engine, as operations can specifically target and modify either local or cloud aspects of an object without affecting the other.

*   **`DriveFile`**: Represents a standard binary file in iCloud Drive. Its content is lazy-loaded, meaning it's only downloaded when explicitly accessed.
*   **`DriveFolder`**: Represents a structural directory in iCloud Drive, managing relationships with child objects.
*   **`Note` (Planned)**: Will serialize between Apple's proprietary formats (protobuf/HTML-like) and local Markdown files.
*   **`Reminder` (Planned)**: Will map Apple's task objects to local JSON or Todo.txt formats.
