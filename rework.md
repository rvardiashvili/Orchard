# Orchard – Linux ↔ Apple Ecosystem Sync Engine

> **Project goal**: Provide a native-feeling iCloud experience on Linux by syncing Apple services (Drive, Notes, Reminders, etc.) with a local filesystem + database, using clear source-of-truth rules, minimal retention, and robust offline support.

---

## 1. High-level Philosophy

Orchard is **not** a file mirror.
It is a **state-synchronized system** with three representations of the same data:

1. **Apple iCloud (authoritative remote)**
2. **Local database (state + metadata)**
3. **Filesystem (user-facing projection)**

Filesystem is **never** the source of truth.
It is a *view* of structured state stored in the DB.

---

## 2. Naming & Layout

### Public name / repo

**Orchard**

* Friendly
* Apple-adjacent metaphor
* Scales well (Drive, Notes, Reminders = different trees)

### Engine name (internal)

Still **Orchard** — no split naming.

---

## 3. Filesystem Layout (User-visible)

```text
~/iCloud/
├── Drive/
│   └── (files & folders)
├── Notes/
│   └── *.orchard-note
├── Reminders/
│   └── *.orchard-reminder
└── Calendar/
```

### Internal Orchard Data

```text
~/.cache/orchard/
├── db.sqlite
├── objects/
├── logs/
└── state.json
```

✔️ This is a **good approach**

* Follows XDG spec
* Allows nuking cache without data loss
* Keeps user namespace clean

---

## 4. Services to Integrate

### Phase 1 (Core)

* iCloud Drive
* Notes
* Reminders
* Contacts (read-only first)
* Calendar (read-only first)

### Phase 2

* Photos (metadata-only first)
* Mail (unlikely / very hard)

---

## 5. Caching Strategy

### Drive

* Download-on-access
* Minimal retention
* Evict using LRU
* Verify freshness via metadata (etag / mod-time)

### Services (Notes, Reminders, etc.)

* Cached **until cloud change detected**
* Revalidate on access if internet available
* Offline edits stored locally & queued

---

## 6. Source of Truth & Conflict Logic

### Priority Order

1. **Apple iCloud** (global truth)
2. **Local database** (offline truth)
3. **Filesystem** (projection only)

### Key Rule

> **Last-modification time + version hash decides**

But with **direction awareness**:

* If local modified while offline → push
* If cloud modified later → pull
* If both modified → conflict object

---

## 7. Core Architecture

```text
┌────────────┐
│ Apple APIs │
└─────┬──────┘
      │
┌─────▼──────────┐
│ Cloud Adapters │
└─────┬──────────┘
      │
┌─────▼──────────┐
│ Sync Engine    │
│ (decision)     │
└─────┬──────────┘
      │
┌─────▼──────────┐
│ Local Database │
└─────┬──────────┘
      │
┌─────▼──────────┐
│ FS Projection  │
└────────────────┘
```

---

## 8. Database – Why You Need One (Yes, Even for Drive)

Filesystem:

* Loses metadata
* No conflict state
* No offline intent

Database:

* Tracks versions
* Stores sync flags
* Decides direction

✔️ **Drive must also have DB entries**

---

## 9. Database Schema (SQLite)

### objects

```sql
CREATE TABLE objects (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  cloud_id TEXT,
  parent_id TEXT,

  content_hash TEXT,
  cloud_hash TEXT,

  local_modified_at INTEGER,
  cloud_modified_at INTEGER,

  sync_state TEXT, -- 'synced', 'dirty_local', 'dirty_cloud', 'pending_push', 'pending_pull', 'conflict', 'deleted_local', 'deleted_cloud'
  dirty INTEGER DEFAULT 0,
  deleted INTEGER DEFAULT 0,

  last_seen_cloud INTEGER,
  last_synced INTEGER
);
```

### drive_cache

```sql
CREATE TABLE drive_cache (
  object_id TEXT PRIMARY KEY,
  local_path TEXT,
  size INTEGER,
  last_accessed INTEGER,
  pinned INTEGER DEFAULT 0
);
```

### sync_queue

```sql
CREATE TABLE sync_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_id TEXT,
  direction TEXT,
  priority INTEGER,
  reason TEXT
);
```

---

## 10. Object Model (Classes over Dicts)

### Why Classes

* Encapsulate logic
* Avoid spaghetti sync code
* Each object knows how to sync itself

### Base Class

```python
class OrchardObject:
    def mark_dirty(self): ...
    def needs_sync(self): ...
    def resolve_conflict(self): ...
    def commit(self):
        """Persists object changes to local DB, updates metadata (e.g., local_modified_at),
        and sets dirty flag. Does NOT interact with the cloud."""
```

### Example: Note

```python
class Note(OrchardObject):
    def update_content(self, text):
        self.text = text
        self.mark_dirty()

    def sync(self, cloud):
        if self.dirty:
            cloud.push_note(self)
        else:
            cloud.pull_note(self)
```

---

## 11. Virtual Files (.orchard-*)

These are **serialization formats**, not truth.

```json
{
  "id": "uuid",
  "type": "note",
  "title": "Shopping",
  "text": "Milk",
  "version": 12
}
```

Filesystem watcher updates DB → DB schedules sync.

---

## 12. Sync Flagging Logic

### When to Flag for Sync

* Local edit → dirty = true → enqueue PUSH
* Cloud change detected → enqueue PULL
* Hash mismatch → enqueue RESOLVE

### Function

```python
def flag_for_sync(obj, reason):
    if obj.local_modified_at > obj.cloud_modified_at:
        enqueue(obj, "push", reason)
    elif obj.cloud_modified_at > obj.local_modified_at:
        enqueue(obj, "pull", reason)
```

---

## 13. Cloud Modification Detection (Example)

```python
def check_cloud_changes():
    updates = cloud.fetch_delta(last_token)
    for item in updates:
        obj = db.get(item.id)
        if not obj or item.modified_at > obj.cloud_modified_at:
            obj.cloud_modified_at = item.modified_at
            enqueue(obj, "pull", "cloud_update")
```

---

## 14. Sync Engine Loop

```python
while True:
    task = sync_queue.next()
    obj = load_object(task.object_id)

    if task.direction == "push":
        obj.push_to_cloud()
    elif task.direction == "pull":
        obj.pull_from_cloud()

    obj.last_synced = now()
    obj.dirty = False
```

---

## 15. Language Choice

### Python is OK if:

* Async IO (`asyncio`)
* SQLite
* Heavy use of caching

### Consider switching later if:

* Huge file throughput
* Real-time FS driver

**Recommendation**:
Start in **Python**, isolate core engine → rewrite later if needed.

---

## 16. Final Mental Model

* DB = brain
* Cloud = authority
* FS = illusion
* Objects = actors
* Sync engine = judge

Orchard doesn’t sync files.
It syncs **intent + state**.

---

## 17. Authentication & Authorization

Orchard does **not** use OAuth in the traditional sense, as Apple does not expose a public OAuth flow for iCloud consumer services.

### Chosen approach

**Apple ID + App-Specific Password**

* User generates an app-specific password in Apple ID settings
* Orchard stores it securely via:

  * `libsecret` / system keyring (preferred)
  * never plaintext on disk

### Why this approach

* Matches how third‑party tools (e.g. davfs, some iCloud clients) operate
* Avoids violating Apple ToS with credential scraping
* Stable across sessions

Authentication token lifecycle:

* Initial login → obtain session cookies / auth tokens
* Tokens cached in memory + encrypted store
* Automatic re-auth on expiry

Orchard **never** logs credentials.

---

## 18. Error Handling & Resilience

Orchard is designed as an **eventually consistent system**.
Failures are expected and survivable.

### iCloud API Failures

Handled categories:

* Network unavailable
* Timeouts
* Rate limiting (HTTP 429)
* Server errors (5xx)

### Retry Strategy

* Exponential backoff with jitter
* Per-object retry counters
* Global circuit breaker if Apple endpoints fail repeatedly

Example:

* Retry after 2s → 5s → 15s → 60s
* Abort after max retries, keep task queued

### Data Integrity Guarantees

* All mutations are **transactional** (SQLite transactions)
* Sync operations are idempotent
* Dirty flags are only cleared **after confirmed cloud success**

If Orchard crashes mid-sync:

* DB remains consistent
* Task stays in queue
* Sync resumes safely

---

## 19. Logging & Observability

Logging is structured and tiered.

### Log Levels

* DEBUG – sync decisions, diffs, metadata
* INFO – sync start/end, successful operations
* WARN – retries, degraded mode
* ERROR – failed syncs, conflicts, corruption

### What Gets Logged

* Object ID + type
* Sync direction (push / pull / delete)
* Conflict creation
* API errors (sanitized)

Logs live in:

```text
~/.cache/orchard/logs/
```

Future extension:

* JSON logs
* Prometheus-style metrics

---

## 20. `sync_state` Field – Explicit Values

The `sync_state` column represents **current reconciliation status**.

Allowed values:

* `synced` – local and cloud aligned
* `dirty_local` – local intent pending
* `dirty_cloud` – cloud change detected
* `pending_push` – queued for upload
* `pending_pull` – queued for download
* `conflict` – manual or automatic resolution required
* `deleted_local` – locally deleted, not yet pushed
* `deleted_cloud` – deleted remotely

This field is **derived but persisted** for debuggability and recovery.

---

## 21. `OrchardObject.commit()` – Responsibilities

`commit()` **does not sync**.

It performs:

* Validation of object invariants
* Serialization to DB representation
* Updating:

  * `local_modified_at`
  * `dirty = true`
  * `sync_state = dirty_local`

Example responsibilities:

* Called after local edit
* Called after applying cloud data
* Called after conflict resolution

Actual cloud IO is **never** done here.

---

## 22. Deletion Handling (Critical)

Deletions are treated as **state transitions**, not immediate erasure.

### Local Deletion

1. User deletes file / note
2. Orchard marks:

   * `deleted = 1`
   * `sync_state = deleted_local`
3. Enqueue cloud delete
4. Cloud confirms → object purged or tombstoned

### Cloud Deletion

1. Cloud delta indicates deletion
2. Orchard marks:

   * `sync_state = deleted_cloud`
3. Local projection removed
4. DB entry kept as tombstone (for idempotency)

### Deletion Conflicts

Cases:

* Locally edited + cloud deleted
* Locally deleted + cloud edited

Resolution strategies:

* Restore and re‑push
* Duplicate as conflict copy
* Honor cloud (configurable)

**Never silently discard user data.**

---

## 23. Final Architecture Summary

* Objects encapsulate state, not sync
* DB is the brain
* Cloud is authoritative but not absolute
* Filesystem is a projection
* Sync is trigger‑driven and centralized

Orchard is designed to be:

* Offline‑first
* Recoverable
* Inspectable
* Predictable

This architecture intentionally mirrors production‑grade sync systems.

---
