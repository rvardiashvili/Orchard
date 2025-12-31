import logging
import time
import os
import hashlib
import sqlite3

from src.objects.base import OrchardObject
from src.db.orchardDB import OrchardDB

logger = logging.getLogger(__name__)

ORCHARD_CACHE_DIR = os.path.expanduser("~/.cache/orchard/objects")

class DriveObject(OrchardObject):
    def __init__(self, db: OrchardDB, row=None):
        super().__init__(db, row)
        row_dict = dict(row) if row else {}
        self.cloud_id = row_dict.get('cloud_id')
        self.cloud_parent_id = row_dict.get('cloud_parent_id')
        self.etag = row_dict.get('etag')
        self.size = row_dict.get('size', 0)
        self.type = row_dict.get('type', 'unknown')
        # Explicitly load extension to ensure it is available for FUSE reconstruction
        self.extension = row_dict.get('extension')
        self.deleted = row_dict.get('deleted', 0)

        self._cache_row = None
        if self.id:
            self._cache_row = self.db.fetchone("SELECT * FROM drive_cache WHERE object_id = ?", (self.id,))
        
        self.local_path = self._cache_row['local_path'] if self._cache_row else None
        self.present_locally = self._cache_row['present_locally'] if self._cache_row else 0
        self.last_accessed = self._cache_row['last_accessed'] if self._cache_row else 0
        self.open_count = self._cache_row['open_count'] if self._cache_row else 0

    def get_local_full_path(self):
        return os.path.join(ORCHARD_CACHE_DIR, self.id)

    def update_cache_entry(self):
        self.db.execute("""
            INSERT OR REPLACE INTO drive_cache (object_id, local_path, size, present_locally, last_accessed, open_count)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (self.id, self.get_local_full_path(), self.size, self.present_locally, self.last_accessed, self.open_count))

class DriveFolder(DriveObject):
    def __init__(self, db, row=None):
        super().__init__(db, row)

    def get_child(self, name):
        # 1. Try finding exact match (folders, or files where name=full_name)
        row = self.db.fetchone("SELECT * FROM objects WHERE parent_id = ? AND name = ?", (self.id, name))
        if row: return OrchardObject.load(self.db, row['id'])

        # 2. Try splitting extension (standard files)
        if '.' in name:
            base, ext = name.rsplit('.', 1)
            row = self.db.fetchone("SELECT * FROM objects WHERE parent_id = ? AND name = ? AND extension = ?", (self.id, base, ext))
            if row: return OrchardObject.load(self.db, row['id'])
            
        return None

    @classmethod
    def create_new_folder(cls, db: OrchardDB, parent_id: str, name: str):
        new_id = f"folder-{os.urandom(8).hex()}"
        now = int(time.time())
        db.execute("""
            INSERT INTO objects (id, type, name, parent_id, local_modified_at, dirty, sync_state)
            VALUES (?, 'folder', ?, ?, ?, 1, 'dirty_local')
        """, (new_id, name, parent_id, now))
        
        db.execute("INSERT INTO drive_cache (object_id, present_locally) VALUES (?, 0)", (new_id,))
        # No enqueue here; handled by FS layer or caller
        return cls(db, db.fetchone("SELECT * FROM objects WHERE id = ?", (new_id,)))

class DriveFile(DriveObject):
    def __init__(self, db, row=None):
        super().__init__(db, row)

    def read_local(self, size, offset):
        path = self.get_local_full_path()
        with open(path, 'rb') as f:
            f.seek(offset)
            return f.read(size)

    def write_local(self, data, offset):
        path = self.get_local_full_path()
        with open(path, 'r+b' if os.path.exists(path) else 'wb') as f:
            f.seek(offset)
            f.write(data)
        
        self.size = os.path.getsize(path)
        self.present_locally = 1
        self.dirty = 1
        self.local_modified_at = int(time.time())
        self.update_cache_entry()
        self.commit()
        return len(data)

    def create_local_placeholder(self):
        path = self.get_local_full_path()
        with open(path, 'wb') as f:
            pass
        self.present_locally = 1
        self.update_cache_entry()

    def _calculate_file_hash(self, file_path):
        hasher = hashlib.sha256()
        try:
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b''):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except FileNotFoundError:
            return None
        
    @classmethod
    def create_new_file(cls, db: OrchardDB, parent_id: str, name: str):
        # We split the name to store in DB as name + extension
        base = name
        ext = None
        if '.' in name:
            base, ext = name.rsplit('.', 1)

        new_id = f"file-{os.urandom(16).hex()}"
        now = int(time.time())
        
        db.execute("""
            INSERT INTO objects (id, type, name, extension, parent_id, size, local_modified_at, dirty, sync_state)
            VALUES (?, 'file', ?, ?, ?, 0, ?, 1, 'dirty_local')
        """, (new_id, base, ext, parent_id, now))
        
        # Create empty file immediately
        obj = cls(db, db.fetchone("SELECT * FROM objects WHERE id = ?", (new_id,)))
        obj.create_local_placeholder()
        
        # No enqueue here; handled by FS layer
        return obj