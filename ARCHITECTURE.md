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

The database (`db.sqlite`) tracks every object's metadata and synchronization status.

### `objects` Table
Stores the persistent state of every file, folder, note, or reminder.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | TEXT | Local unique UUID. |
| `cloud_id` | TEXT | Apple's server-side ID (Document ID). |
| `parent_id` | TEXT | Local parent folder UUID. |
| `etag` | TEXT | Cloud HTTP ETag for versioning. |
| `sync_state` | TEXT | Current status: `synced`, `dirty`, `conflict`, `pending_push`. |
| `local_modified_at` | INT | Unix timestamp of last local edit. |
| `missing_from_cloud` | INT | Flag if 404 received from server. |

### `actions` Table
A persistent task queue for the Sync Engine.

| Column | Type | Description |
| :--- | :--- | :--- |
| `action_type` | TEXT | `upload`, `download`, `move`, `delete`, `list_children`. |
| `target_id` | TEXT | The object ID to act upon. |
| `status` | TEXT | `pending`, `processing`, `failed`, `completed`. |
| `priority` | INT | Higher values run first (e.g., `open()` triggers priority 1). |

## 4. Synchronization Logic

### The "Lazy Open" Strategy
To prevent file managers (Nautilus, Dolphin) from freezing the UI:
1.  `open()` calls return a file handle **immediately**.
2.  If the file is not cached, we do **not** block.
3.  We only block during the `read()` syscall, and only if the content is missing.
4.  We check the calling process PID. Known thumbnailers (`ffmpeg`, `nautilus-thumbnailer`) are **denied** download access to save bandwidth.

### Conflict Resolution (412 Precondition Failed)
When uploading a file that conflicts with a remote change:
1.  Orchard detects the `412` error.
2.  It lists the remote directory to find the conflicting file ID.
3.  It enforces **Local Wins** for user-initiated actions: it deletes the remote conflicting file and retries the upload.

### Atomic Writes
1.  **Uploads**: We create a symlink in a temporary directory to ensure the upload has the correct filename without moving the actual cache file.
2.  **Downloads**: We download to `filename.part` and use `os.rename` to atomically swap it into place, ensuring no partial reads.

## 5. Object Model

Orchard uses specific classes to handle different data types:

*   **`DriveFile`**: Standard binary files. Content is lazy-loaded.
*   **`DriveFolder`**: Structural nodes.
*   **`Note` (Planned)**: Serializes between Apple's protobuf/HTML-like format and local Markdown.
*   **`Reminder` (Planned)**: Maps Apple's task objects to local JSON or Todo.txt formats.
