import sqlite3
import os
import threading
import logging
import time
import json # New import

from src.config.sync_config import MAX_RETRIES # Import MAX_RETRIES from new config file
from src.config.sync_states import SYNC_STATE_ERROR # Import SYNC_STATE_ERROR from new config file

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL, -- 'file', 'folder'
    parent_id TEXT,
    
    -- Metadata
    name TEXT,
    size INTEGER DEFAULT 0,
    extension TEXT,
    
    -- Cloud Metadata
    cloud_id TEXT,
    cloud_parent_id TEXT,
    etag TEXT,
    missing_from_cloud INTEGER DEFAULT 0,
    
    -- Modification Times (Unix Timestamp)
    local_modified_at INTEGER DEFAULT 0,
    cloud_modified_at INTEGER DEFAULT 0,
    
    revision TEXT, -- Cloud revision ID
    origin TEXT DEFAULT 'local', -- 'local' or 'cloud'
    
    -- Sync State
    sync_state TEXT DEFAULT 'synced', 
    dirty INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0,
    
    last_synced INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS drive_cache (
    object_id TEXT PRIMARY KEY,
    local_path TEXT,
    
    size INTEGER DEFAULT 0,
    file_hash TEXT,
    present_locally INTEGER DEFAULT 0,
    pinned INTEGER DEFAULT 0,
    
    last_accessed INTEGER DEFAULT 0,
    open_count INTEGER DEFAULT 0,
    FOREIGN KEY(object_id) REFERENCES objects(id) ON DELETE CASCADE
);

-- New Actions Queue Table
CREATE TABLE IF NOT EXISTS actions (
    action_id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL, -- 'upload', 'update_content', 'rename', 'move', 'delete', 'download', 'list_children'
    target_id TEXT NOT NULL, -- The object ID being acted upon
    destination TEXT, -- For 'move' (new parent_id) or 'rename' (new name)
    metadata TEXT, -- New: Stores JSON-serialized extra data for the action
    direction TEXT NOT NULL, -- 'push' (Local->Cloud) or 'pull' (Cloud->Local)
    priority INTEGER DEFAULT 0, -- Higher number = higher priority
    created_at INTEGER,
    status TEXT DEFAULT 'pending', -- 'pending', 'processing', 'completed', 'failed'
    retry_count INTEGER DEFAULT 0,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_parent ON objects(parent_id);
CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status);
CREATE INDEX IF NOT EXISTS idx_actions_target ON actions(target_id);
"""

class OrchardDB:
    _instance = None
    _lock = threading.Lock()

    def __init__(self, db_path):
        self.db_path = db_path
        self.local_thread = threading.local()
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)
            # Bootstrapping Roots - ONLY Drive
            conn.execute("INSERT OR IGNORE INTO objects (id, type, name, parent_id) VALUES ('root', 'folder', 'root', NULL)")
            conn.execute("INSERT OR IGNORE INTO objects (id, type, name, parent_id) VALUES ('drive_root', 'folder', 'Drive', 'root')")

    def get_conn(self):
        if not hasattr(self.local_thread, 'conn'):
            self.local_thread.conn = sqlite3.connect(self.db_path, timeout=30.0) # Increased timeout
            self.local_thread.conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrency
            self.local_thread.conn.execute("PRAGMA journal_mode=WAL;")
        return self.local_thread.conn

    def execute(self, query, params=()):
        conn = self.get_conn()
        try:
            cur = conn.execute(query, params)
            conn.commit()
            return cur
        except Exception as e:
            logger.error(f"DB Error: {e} | Query: {query}", exc_info=True)
            raise

    def fetchone(self, query, params=()):
        cur = self.get_conn().execute(query, params)
        return cur.fetchone()

    def fetchall(self, query, params=()):
        cur = self.get_conn().execute(query, params)
        return cur.fetchall()

    def enqueue_action(self, target_id, action_type, direction, destination=None, metadata=None, priority=0):
        """
        Enqueues a specific action into the actions table.
        """
        # Deduplication: Check if an equivalent action is already pending/processing
        check_types = [action_type]
        # If updating content, also check if an initial upload is pending (which covers content)
        if action_type == 'update_content':
            check_types.append('upload')
            
        placeholders = ','.join(['?'] * len(check_types))
        query = f"""
            SELECT 1 FROM actions 
            WHERE target_id=? AND action_type IN ({placeholders}) AND direction=? AND status IN ('pending', 'processing')
        """
        params = [target_id] + check_types + [direction]
        
        existing = self.fetchone(query, tuple(params))
        
        if existing:
            # If metadata differs significantly we might want to update it, but for simple push/pull usually skipping is fine.
            # Especially for 'update_content' which just means "sync the file".
            # logger.debug(f"Skipping duplicate action {action_type} for {target_id}")
            return

        metadata_json = json.dumps(metadata) if metadata else None
        
        conn = self.get_conn()
        try:
            # Explicit transaction start?
            # conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                INSERT INTO actions (target_id, action_type, direction, destination, metadata, priority, created_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            """, (target_id, action_type, direction, destination, metadata_json, priority, int(time.time())))
            conn.commit()
        except Exception as e:
            logger.error(f"DB Error enqueuing action: {e}", exc_info=True)
            raise

    def get_next_action(self):
        """
        Retrieves the next pending action from the queue (FIFO).
        """
        conn = self.get_conn()
        # Fetch oldest pending action
        row = conn.execute("""
            SELECT * FROM actions 
            WHERE status = 'pending' 
            ORDER BY priority DESC, created_at ASC 
            LIMIT 1
        """).fetchone()
        
        if row:
            # Mark as processing to prevent re-fetching by other threads (if we go multi-threaded later)
            self.execute("UPDATE actions SET status = 'processing' WHERE action_id = ?", (row['action_id'],))
            return dict(row)
        return None

    def complete_action(self, action_id):
        """Marks an action as completed (removes it from queue)."""
        self.execute("DELETE FROM actions WHERE action_id = ?", (action_id,))
        # Alternatively, for audit log: 
        # self.execute("UPDATE actions SET status = 'completed' WHERE action_id = ?", (action_id,))

    def fail_action(self, action_id, target_obj_id, error_msg=None):
        """Marks an action as failed and potentially updates object sync_state to ERROR."""
        self.execute("""
            UPDATE actions 
            SET status = 'failed', last_error = ?, retry_count = retry_count + 1 
            WHERE action_id = ?
        """, (str(error_msg), action_id))

        # Check if retry limit exceeded
        row = self.fetchone("SELECT retry_count FROM actions WHERE action_id = ?", (action_id,))
        if row and row['retry_count'] > MAX_RETRIES:
            logger.error(f"Action {action_id} for object {target_obj_id} exceeded max retries. Setting object sync_state to ERROR.", exc_info=True)
            self.execute("UPDATE objects SET sync_state = ? WHERE id = ?", (SYNC_STATE_ERROR, target_obj_id))
            # Also, remove the action from the queue as it won't be retried further
            self.execute("DELETE FROM actions WHERE action_id = ?", (action_id,))

def get_db(path=None):
    if OrchardDB._instance is None and path:
        with OrchardDB._lock:
            if OrchardDB._instance is None:
                OrchardDB._instance = OrchardDB(path)
    return OrchardDB._instance