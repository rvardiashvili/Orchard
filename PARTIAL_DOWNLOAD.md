Partial Download (Sparse Caching) Strategy

1. Overview

The goal of this strategy is to allow users to access large files on iCloud instantly without waiting for the entire file to download. This is achieved by downloading only the requested "chunks" (blocks) of data on-demand.

2. Core Concept: Fixed-Block Sparse Caching

Instead of tracking arbitrary byte ranges (which leads to complex fragmentation logic), we divide every file into fixed-size blocks.

Block Size (CHUNK_SIZE): 8 MB (8 * 1024 * 1024 bytes).

Rationale: Large enough to be efficient for network throughput (reducing HTTP overhead), but small enough to provide responsive "seeking" in video players.

A 100MB file would effectively be a collection of ~13 blocks (indexes 0 to 12). The system tracks exactly which of these 13 blocks are present locally.

3. Database Schema Changes

To track the presence of individual blocks, a new table is required in src/db/orchardDB.py.

CREATE TABLE IF NOT EXISTS chunk_cache (
    object_id TEXT,
    chunk_index INTEGER,
    last_accessed INTEGER, -- For future LRU eviction
    PRIMARY KEY (object_id, chunk_index),
    FOREIGN KEY(object_id) REFERENCES objects(id) ON DELETE CASCADE
);


Additionally, the drive_cache table's present_locally field (or a new field) should support a "Partial" state:

0: Cloud Only (Ghost)

1: Fully Present

2: Partially Present (Sparse)

4. Workflow Logic

A. File Open (open)

When a file is opened that is not fully present (present_locally != 1):

Sparse Initialization: Create a local file on the disk (if not exists).

Truncate: Use os.truncate() to set the physical file size to the actual file size reported by the cloud. This creates a "sparse file" which occupies almost 0 bytes on disk but appears full size to the OS.

State Update: Mark the file as present_locally = 2 (Partial) in the DB.

B. Read Operation (read)

This is the critical path in src/fs/orchardFS.py.

Input: offset (byte start), size (bytes requested).

Map to Chunks:

start_chunk = offset // CHUNK_SIZE

end_chunk = (offset + size - 1) // CHUNK_SIZE

Check Availability:

Query chunk_cache for all chunk_index values between start_chunk and end_chunk.

Handle Missing Chunks:

Identify which chunks are missing.

Enqueue Action: For each missing chunk, enqueue a download_chunk action.

Metadata: {'chunk_index': i}.

Priority: High (10).

Wait: Enter a loop checking the DB until the chunks appear (or timeout).

Read Data:

Once chunks are confirmed present, perform a standard seek() and read() on the local sparse file.

C. Sync Engine Execution (_handle_download_chunk)

The SyncEngine in src/sync/engine.py processes the download_chunk action.

Calculate Byte Range:

start_byte = chunk_index * CHUNK_SIZE

end_byte = min((chunk_index + 1) * CHUNK_SIZE - 1, total_file_size - 1)

HTTP Range Request:

Send GET request to iCloud with header: Range: bytes={start_byte}-{end_byte}.

Sparse Write:

Open the local file in r+b mode (Read/Write Binary). Do not use 'w' or 'a'.

seek(start_byte)

write(response.content)

Update DB:

Insert (object_id, chunk_index) into chunk_cache.

Optimization: Check if all chunks are now present. If so, promote file to present_locally = 1.

5. Hybrid Strategy (Optimization)

To avoid overhead for small files, we implement a hybrid approach:

Small Files (< 32MB): Always use Full Download.

When read/open is requested, download the entire file in one go. It's faster than managing chunks.

Large Files (>= 32MB): Use Sparse Caching.

Only download requested chunks.

6. Offline Safety & "Pinning"

Since sparse files are incomplete, they are "corrupt" if the user goes offline. To mitigate this:

Pinning: Implement a "Make Available Offline" feature (via setxattr).

Logic: When a file is pinned, the engine iterates through all chunks (0 to N) and downloads any that are missing, effectively converting it to a Full Download.

7. Edge Cases

Write Operations: If a user writes to a sparse file, the simplest strategy is to mark the file as "Dirty" and treat it as fully local moving forward (or trigger a full download of remaining parts before allowing the write to ensure consistency).

Cache Eviction: A background job can periodically delete rows from chunk_cache and use fallocate(..., FALLOC_FL_PUNCH_HOLE, ...) on the local file to free up disk space for chunks that haven't been accessed recently.

Remote Changes (ETag Mismatch): If the file changes on the cloud (new ETag), any locally cached chunks are potentially invalid.

Action: On detecting a remote change (during list_children or metadata pull), flush the local chunk_cache for that object and re-download chunks as needed.

Network Failure: If a chunk download fails after retries:

The FUSE read() call will timeout or receive an error signal from the DB.

Return errno.EIO (Input/output error) to the application.

Disk Full (ENOSPC): If writing a chunk fails due to disk space:

Fail Action: The download_chunk action must fail and log the error.

Return Error: The waiting FUSE read call should receive errno.ENOSPC.

Trigger Eviction: The system should immediately trigger the Cache Eviction routine (deleting unpinned chunks from LRU files) to free up space, then potentially auto-retry the download if space becomes available.