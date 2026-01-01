import sqlite3
import os
import threading
import logging
import time
import json 

from src.config.sync_config import MAX_RETRIES 
from src.config.sync_states import SYNC_STATE_ERROR 

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

CREATE TABLE IF NOT EXISTS shadows (
    object_id TEXT PRIMARY KEY,
    cloud_id TEXT,
    parent_id TEXT,
    name TEXT,
    etag TEXT,
    file_hash TEXT,
    modified_at INTEGER,
    FOREIGN KEY(object_id) REFERENCES objects(id) ON DELETE CASCADE
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

CREATE TABLE IF NOT EXISTS actions (
    action_id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL, 
    target_id TEXT NOT NULL, 
    destination TEXT, 
    metadata TEXT, 
    direction TEXT NOT NULL, 
    priority INTEGER DEFAULT 0, 
    created_at INTEGER,
    status TEXT DEFAULT 'pending', 
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
        self.db_path = os.path.abspath(db_path)
        self.local_thread = threading.local()
        self._init_db()

    def _init_db(self):
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
            
        # Retry logic for initial connection to handle filesystem race conditions
        for attempt in range(3):
            try:
                with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                    # Removed WAL mode for stability during fresh start
                    # conn.execute("PRAGMA journal_mode=WAL;") 
                    conn.executescript(SCHEMA)
                    conn.execute("INSERT OR IGNORE INTO objects (id, type, name, parent_id) VALUES ('root', 'folder', 'root', NULL)")
                    conn.execute("INSERT OR IGNORE INTO objects (id, type, name, parent_id) VALUES ('drive_root', 'folder', 'Drive', 'root')")
                    conn.commit()
                break
            except sqlite3.OperationalError as e:
                if "disk I/O error" in str(e) or "database is locked" in str(e):
                    if attempt < 2:
                        time.sleep(0.5)
                        continue
                raise e

    def get_conn(self):
        if not hasattr(self.local_thread, 'conn'):
            # Connect with a reasonable timeout
            self.local_thread.conn = sqlite3.connect(self.db_path, timeout=60.0)
            self.local_thread.conn.row_factory = sqlite3.Row
        return self.local_thread.conn

    def execute(self, query, params=()):
        conn = self.get_conn()
        try:
            cur = conn.execute(query, params)
            conn.commit()
            return cur
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) or "disk I/O error" in str(e):
                logger.warning(f"DB locked/IO error, retrying: {e}")
                time.sleep(0.1)
                try:
                    cur = conn.execute(query, params)
                    conn.commit()
                    return cur
                except Exception as retry_e:
                    logger.error(f"DB Retry Failed: {retry_e}")
                    raise
            logger.error(f"DB Error: {e} | Query: {query}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"DB Error: {e} | Query: {query}", exc_info=True)
            raise

    def fetchone(self, query, params=()):
        try:
            cur = self.get_conn().execute(query, params)
            return cur.fetchone()
        except sqlite3.OperationalError as e:
            if "disk I/O error" in str(e):
                logger.warning(f"DB IO Error on fetch, retrying: {e}")
                time.sleep(0.1)
                cur = self.get_conn().execute(query, params)
                return cur.fetchone()
            raise

    def fetchall(self, query, params=()):
        try:
            cur = self.get_conn().execute(query, params)
            return cur.fetchall()
        except sqlite3.OperationalError as e:
            if "disk I/O error" in str(e):
                logger.warning(f"DB IO Error on fetchall, retrying: {e}")
                time.sleep(0.1)
                cur = self.get_conn().execute(query, params)
                return cur.fetchall()
            raise

    def update_shadow(self, obj_id, cloud_id=None, parent_id=None, name=None, etag=None, file_hash=None, modified_at=None):
        conn = self.get_conn()
        exists = conn.execute("SELECT 1 FROM shadows WHERE object_id = ?", (obj_id,)).fetchone()
        
        if not exists:
            conn.execute("""
                INSERT INTO shadows (object_id, cloud_id, parent_id, name, etag, file_hash, modified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (obj_id, cloud_id, parent_id, name, etag, file_hash, modified_at))
        else:
            fields, values = [], []
            if cloud_id is not None: fields.append("cloud_id = ?"); values.append(cloud_id)
            if parent_id is not None: fields.append("parent_id = ?"); values.append(parent_id)
            if name is not None: fields.append("name = ?"); values.append(name)
            if etag is not None: fields.append("etag = ?"); values.append(etag)
            if file_hash is not None: fields.append("file_hash = ?"); values.append(file_hash)
            if modified_at is not None: fields.append("modified_at = ?"); values.append(modified_at)
            
            if fields:
                values.append(obj_id)
                conn.execute(f"UPDATE shadows SET {', '.join(fields)} WHERE object_id = ?", tuple(values))
        conn.commit()

    def get_shadow(self, obj_id):
        return self.fetchone("SELECT * FROM shadows WHERE object_id = ?", (obj_id,))

    def delete_shadow(self, obj_id):
        self.execute("DELETE FROM shadows WHERE object_id = ?", (obj_id,))

    def enqueue_action(self, target_id, action_type, direction, destination=None, metadata=None, priority=0):
        conn = self.get_conn()
        meta_dict = metadata if isinstance(metadata, dict) else {}
        
        # 1. Fetch ALL pending, processing, OR FAILED actions for this object
        # Including 'failed' allows us to merge into a failed upload/update and retry it
        pending_actions = conn.execute("""
            SELECT action_id, action_type, metadata, destination, status
            FROM actions 
            WHERE target_id = ? AND status IN ('pending', 'processing', 'failed')
            ORDER BY created_at DESC
        """, (target_id,)).fetchall()

        def update_and_exit(action_id, updates):
            # If we update a 'failed' action, we must reset it to 'pending' to retry
            updates['status'] = 'pending'
            updates['retry_count'] = 0
            updates['last_error'] = None
            
            set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
            vals = list(updates.values()) + [action_id]
            conn.execute(f"UPDATE actions SET {set_clause} WHERE action_id = ?", tuple(vals))
            conn.commit()
            logger.info(f"Coalesced action {action_type} into {action_id} for {target_id}")

        def delete_and_exit(action_ids):
             placeholders = ",".join("?" * len(action_ids))
             conn.execute(f"DELETE FROM actions WHERE action_id IN ({placeholders})", tuple(action_ids))
             conn.commit()
             logger.info(f"Deleted actions {action_ids} due to {action_type} for {target_id}")

        # --- LOGIC START ---

        # SCENARIO: LIST CHILDREN (Deduplication)
        # If we already have a pending/processing list_children for this target, we don't need another one.
        if action_type == 'list_children':
            for row in pending_actions:
                if row['action_type'] == 'list_children':
                    logger.info(f"Skipping duplicate list_children for {target_id}")
                    return 
            # If no duplicate found, fall through to enqueue

        if action_type == 'delete':
            ids_to_delete = [row['action_id'] for row in pending_actions if row['status'] != 'processing']
            if ids_to_delete:
                delete_and_exit(ids_to_delete)

        if action_type == 'rename':
            for row in pending_actions:
                if row['status'] == 'processing': break

                prev_id = row['action_id']
                prev_type = row['action_type']
                prev_meta = json.loads(row['metadata']) if row['metadata'] else {}

                if prev_type == 'rename':
                    prev_meta['to_name'] = meta_dict.get('to_name')
                    update_and_exit(prev_id, {
                        'destination': destination,
                        'metadata': json.dumps(prev_meta)
                    })
                    return 

                if prev_type in ('upload', 'update_content'):
                    prev_meta.update(meta_dict)
                    prev_meta['name'] = meta_dict.get('to_name')
                    update_and_exit(prev_id, {'metadata': json.dumps(prev_meta)})
                    return
                
                if prev_type == 'move': continue
                break

        if action_type == 'move':
            for row in pending_actions:
                if row['status'] == 'processing': break

                prev_id = row['action_id']
                prev_type = row['action_type']
                
                if prev_type == 'move':
                    update_and_exit(prev_id, {'destination': destination})
                    return

                if prev_type == 'rename': continue
                break

        if action_type == 'update_content':
             for row in pending_actions:
                if row['status'] == 'processing': break

                prev_id = row['action_id']
                prev_type = row['action_type']
                prev_meta = json.loads(row['metadata']) if row['metadata'] else {}
                
                if prev_type == 'update_content':
                     prev_meta.update(meta_dict)
                     update_and_exit(prev_id, {'metadata': json.dumps(prev_meta)})
                     return
                
                if prev_type == 'upload':
                     prev_meta.update(meta_dict)
                     update_and_exit(prev_id, {'metadata': json.dumps(prev_meta)})
                     return

                if prev_type in ('rename', 'move'): continue
                break

        # Standard Enqueue
        meta_json = json.dumps(meta_dict) if meta_dict else None
        conn.execute("""
            INSERT INTO actions (target_id, action_type, direction, destination, metadata, priority, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (target_id, action_type, direction, destination, meta_json, priority, int(time.time())))
        conn.commit()

    def get_next_action(self):
        conn = self.get_conn()
        row = conn.execute("""
            SELECT * FROM actions 
            WHERE status = 'pending' 
            ORDER BY priority DESC, created_at ASC 
            LIMIT 1
        """).fetchone()
        
        if row:
            self.execute("UPDATE actions SET status = 'processing' WHERE action_id = ?", (row['action_id'],))
            return dict(row)
        return None

    def complete_action(self, action_id):
        self.execute("DELETE FROM actions WHERE action_id = ?", (action_id,))

    def fail_action(self, action_id, target_obj_id, error_msg=None):
        self.execute("""
            UPDATE actions 
            SET status = 'failed', last_error = ?, retry_count = retry_count + 1 
            WHERE action_id = ?
        """, (str(error_msg), action_id))

        row = self.fetchone("SELECT retry_count FROM actions WHERE action_id = ?", (action_id,))
        if row and row['retry_count'] > MAX_RETRIES:
            logger.error(f"Action {action_id} for {target_obj_id} exceeded max retries. Setting object sync_state to ERROR.")
            self.execute("UPDATE objects SET sync_state = ? WHERE id = ?", (SYNC_STATE_ERROR, target_obj_id))
            self.execute("DELETE FROM actions WHERE action_id = ?", (action_id,))

def get_db(path=None):
    if OrchardDB._instance is None and path:
        with OrchardDB._lock:
            if OrchardDB._instance is None:
                OrchardDB._instance = OrchardDB(path)
    return OrchardDB._instance