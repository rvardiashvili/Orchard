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
| `present_locally` | INTEGER | Flag (0/1) if the file content is present on disk. |
| `pinned` | INTEGER | Flag (0/1) if the file is explicitly pinned for local retention. |
| `last_accessed` | INTEGER | Unix timestamp of the last local access. |
| `open_count` | INTEGER | Number of current open handles to the local file. |

### `actions` Table
A persistent, prioritized task queue for the Sync Engine. Actions can be coalesced and retried with exponential backoff.

| Column | Type | Description |
| :--- | :--- | :--- |
| `action_id` | INTEGER | Unique auto-incrementing ID for the action. |
| `action_type` | TEXT | Type of operation: `upload`, `download`, `move`, `rename`, `delete`, `update_content`, `list_children`, `ensure_latest`. |
| `target_id` | TEXT | The local object ID (from `objects` table) the action pertains to. |
| `destination` | TEXT | For `move`/`rename`, the new parent ID or new name. |
| `metadata` | TEXT | JSON string containing additional context for the action (e.g., original parent ID for moves, new name for renames, file hash for uploads). |
| `direction` | TEXT | `push` (local to cloud) or `pull` (cloud to local). |
| `priority` | INTEGER | Higher values run first (e.g., FUSE-triggered actions get higher priority). |
| `created_at` | INTEGER | Unix timestamp when the action was enqueued. |
| `status` | TEXT | Current status: `pending`, `processing`, `failed`, `completed`. |
| `retry_count` | INTEGER | Number of times a failed action has been retried. |
| `last_error` | TEXT | Last error message if the action failed. |

## 4. Synchronization Logic

The Sync Engine operates asynchronously, driven by the `actions` queue, to reconcile local filesystem state with the remote iCloud state. It employs several strategies to ensure robustness and efficiency.

### The "Lazy Open" Strategy
To prevent file managers (Nautilus, Dolphin) from freezing the UI:
1.  `open()` calls return a file handle **immediately**.
2.  If the file is not cached, we do **not** block.
3.  We only block during the `read()` syscall, and only if the content is missing.
4.  We check the calling process PID. Known thumbnailers (`ffmpeg`, `nautilus-thumbnailer`) are **denied** download access to save bandwidth.

### Action Queueing and Coalescing
The `actions` table functions as a persistent, prioritized task queue. The Sync Engine optimizes operations by:
*   **Prioritization**: Actions triggered by immediate user interaction (e.g., file opens) can be given higher priority.
*   **Coalescing**: Sequential, redundant actions on the same object are merged. For example, multiple renames of the same file before it's synced will result in a single `rename` action with the final target name. This prevents unnecessary network calls.
*   **Retry with Exponential Backoff**: Failed actions are automatically retried with an increasing delay, preventing network congestion and gracefully handling transient errors.

### Robust Conflict Resolution with Shadow State
When a local change conflicts with a remote change (e.g., both edited a file, or a file was deleted remotely but edited locally):
1.  Orchard utilizes the `shadows` table to store a snapshot of the object's cloud metadata before a local modification or after the last known good sync.
2.  When attempting an `upload` or `update_content`, if the cloud's current `etag` does not match the `shadows` `etag` (or the last known `etag` from the `objects` table), a conflict is detected.
3.  For many actions (e.g., `upload`, `update_content`), Orchard prioritizes **Local Wins**: it attempts to delete the conflicting remote item (using its cloud ID and current `etag` to ensure it's deleting the correct version) and then re-uploads the local version. This ensures user-initiated changes are preserved.
4.  More complex conflict scenarios lead to a `sync_state = 'conflict'` and require manual resolution or specific user actions.

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
