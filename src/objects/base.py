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

class OrchardObject:
    def __init__(self, db: OrchardDB, row=None):
        _row_dict = dict(row) if row else {} 
        self.db = db
        self.id = _row_dict.get('id')
        self.type = _row_dict.get('type', 'unknown')
        self.name = _row_dict.get('name', 'Untitled')
        self.parent_id = _row_dict.get('parent_id')
        
        self.local_modified_at = _row_dict.get('local_modified_at', 0)
        self.cloud_modified_at = _row_dict.get('cloud_modified_at', 0)
        self.size = row['size'] if row else 0 
        self.extension = row['extension'] if row and 'extension' in row else None 
        
        self.etag = _row_dict.get('etag')
        self.revision = _row_dict.get('revision') # New: Cloud Revision ID
        self.origin = _row_dict.get('origin', 'local') # New: 'local' or 'cloud'
        self.dirty = _row_dict.get('dirty', 0)
        self.sync_state = _row_dict.get('sync_state', 'synced') # Ensure sync_state is initialized
        
        self._row_data = _row_dict

    @classmethod
    def load(cls, db, object_id):
        row = db.fetchone("SELECT * FROM objects WHERE id = ?", (object_id,))
        if not row: return None
        
        # Drive-Only Factory
        # Imports inside method to avoid circular imports
        from src.objects.drive import DriveFile, DriveFolder

        otype = row['type']
        if otype == 'file': 
            return DriveFile(db, row)
        if otype == 'folder': 
            return DriveFolder(db, row)
        
        return cls(db, row)

    def list_children(self):
        """Returns list of child objects."""
        # Query for IDs so we can load full objects using the factory
        rows = self.db.fetchall("SELECT id FROM objects WHERE parent_id = ?", (self.id,))
        children = []
        for r in rows:
            child = OrchardObject.load(self.db, r['id'])
            if child: 
                children.append(child)
        return children

    def commit(self):
        """Persist metadata changes to DB and mark dirty, setting sync_state to pending_push."""
        now = int(time.time())
        self.db.execute("""
            UPDATE objects SET 
                name = ?, 
                extension = ?, 
                local_modified_at = ?,
                dirty = 1,
                sync_state = ?, -- Changed to use parameter
                revision = ?, -- Include revision in update
                origin = ? -- Include origin in update
            WHERE id = ?
        """, (self.name, self.extension, now, SYNC_STATE_PENDING_PUSH, self.revision, self.origin, self.id))
        
        # We NO LONGER enqueue generic sync here. 
        # Specific actions (rename, move, upload) are enqueued by OrchardFS or Engine.
        logger.debug(f"Committed {self.name} (ID: {self.id})")