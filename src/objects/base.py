import time
import logging
import errno
from src.db.orchardDB import OrchardDB
from src.config.sync_states import (
    SYNC_STATE_SYNCHRONIZED, 
    SYNC_STATE_PENDING_PUSH, 
    SYNC_STATE_PENDING_PULL, 
    SYNC_STATE_CONFLICT, 
    SYNC_STATE_ERROR
)

logger = logging.getLogger(__name__)

class CloudState:
    """Represents the state of the object as known on Apple's servers."""
    def __init__(self, row):
        self.id = row.get('cloud_id')
        self.parent_id = row.get('cloud_parent_id')
        self.etag = row.get('etag')
        self.modified_at = row.get('cloud_modified_at')
        self.revision = row.get('revision')
        self.missing = row.get('missing_from_cloud', 0)

class LocalState:
    """Represents the state of the object on the local device."""
    def __init__(self, row):
        self.parent_id = row.get('parent_id')
        self.name = row.get('name', 'Untitled')
        self.extension = row.get('extension')
        self.size = row.get('size', 0)
        self.modified_at = row.get('local_modified_at', 0)
        self.dirty = row.get('dirty', 0)
        self.sync_state = row.get('sync_state', 'synced')
        self.origin = row.get('origin', 'local')
        
        # Filesystem specific (will be populated by subclasses if applicable)
        self.path = None
        self.present = 0
        self.last_accessed = 0
        self.open_count = 0

class OrchardObject:
    def __init__(self, db: OrchardDB, row=None):
        self.db = db
        _row_dict = dict(row) if row else {} 
        
        self.id = _row_dict.get('id')
        self.type = _row_dict.get('type', 'unknown')
        
        # Initialize State Objects
        self.cloud = CloudState(_row_dict)
        self.local = LocalState(_row_dict)
        
        # Keep raw row for debugging if needed
        self._row_data = _row_dict

    # --- Properties for Backward Compatibility & Convenience ---
    
    @property
    def cloud_id(self): return self.cloud.id
    @cloud_id.setter
    def cloud_id(self, val): self.cloud.id = val

    @property
    def cloud_parent_id(self): return self.cloud.parent_id
    @cloud_parent_id.setter
    def cloud_parent_id(self, val): self.cloud.parent_id = val

    @property
    def etag(self): return self.cloud.etag
    @etag.setter
    def etag(self, val): self.cloud.etag = val
    
    @property
    def parent_id(self): return self.local.parent_id
    @parent_id.setter
    def parent_id(self, val): self.local.parent_id = val

    @property
    def name(self): return self.local.name
    @name.setter
    def name(self, val): self.local.name = val

    @property
    def extension(self): return self.local.extension
    @extension.setter
    def extension(self, val): self.local.extension = val

    @property
    def size(self): return self.local.size
    @size.setter
    def size(self, val): self.local.size = val

    @property
    def local_modified_at(self): return self.local.modified_at
    @local_modified_at.setter
    def local_modified_at(self, val): self.local.modified_at = val

    @property
    def dirty(self): return self.local.dirty
    @dirty.setter
    def dirty(self, val): self.local.dirty = val
    
    @property
    def sync_state(self): return self.local.sync_state
    @sync_state.setter
    def sync_state(self, val): self.local.sync_state = val

    # --- Methods ---

    @classmethod
    def load(cls, db, object_id):
        row = db.fetchone("SELECT * FROM objects WHERE id = ?", (object_id,))
        if not row: return None
        
        # Drive-Only Factory (Lazy import to avoid circular dependency)
        from src.objects.drive import DriveFile, DriveFolder

        otype = row['type']
        if otype == 'file': 
            return DriveFile(db, row)
        if otype == 'folder': 
            return DriveFolder(db, row)
        
        return cls(db, row)

    def list_children(self):
        """Returns list of child objects."""
        rows = self.db.fetchall("SELECT id FROM objects WHERE parent_id = ?", (self.id,))
        children = []
        for r in rows:
            child = OrchardObject.load(self.db, r['id'])
            if child: 
                children.append(child)
        return children

    def commit(self):
        """Persist ALL local state changes to DB immediately."""
        now = int(time.time())
        self.db.execute("""
            UPDATE objects SET 
                name = ?, 
                extension = ?, 
                parent_id = ?,   -- CRITICAL: Persist move operations
                size = ?,        -- CRITICAL: Persist size changes
                local_modified_at = ?,
                dirty = 1,
                sync_state = ?, 
                revision = ?, 
                origin = ? 
            WHERE id = ?
        """, (
            self.local.name, 
            self.local.extension, 
            self.local.parent_id,
            self.local.size,
            now, 
            SYNC_STATE_PENDING_PUSH, 
            self.cloud.revision, 
            self.local.origin, 
            self.id
        ))
        logger.debug(f"Committed local state for {self.local.name} (ID: {self.id})")