import threading
import time
import logging
import os
import json # New import for JSON handling

from src.db.orchardDB import OrchardDB
from src.icloud_client.icloud_drive import iCloudDrive, CLOUD_DOCS_ZONE_ID_ROOT
from src.icloud_client.client import OrchardiCloudClient
from src.objects.drive import DriveFile, DriveFolder 
from src.objects.base import OrchardObject
from src.config.sync_states import (
    SYNC_STATE_SYNCHRONIZED, 
    SYNC_STATE_PENDING_PUSH, 
    SYNC_STATE_PENDING_PULL, 
    SYNC_STATE_CONFLICT, 
    SYNC_STATE_ERROR
)
from src.config.sync_config import MAX_RETRIES, BASE_BACKOFF_SECONDS # Import from new config file

logger = logging.getLogger(__name__)

class SyncEngine:
    def __init__(self, db: OrchardDB, api_client: OrchardiCloudClient):
        self.db = db
        self.api = api_client
        self.running = False
        self.drive_svc: iCloudDrive = None
        
        if self.api and self.api.authenticated:
            try:
                ds_root = self.api.get_webservice_url("drivews")
                doc_root = self.api.get_webservice_url("docws")
                if ds_root and doc_root:
                    self.drive_svc = iCloudDrive(self.api.session, ds_root, doc_root, self.api._pyicloud_service.params)
                    print("iCloudDrive service initialized.")
                    print("ds_root:", ds_root)
                    print("doc_root:", doc_root)
                    print("params:", self.api._pyicloud_service.params)
                    logger.info("iCloudDrive service initialized.")
            except Exception as e:
                logger.error(f"Failed to initialize Drive service: {e}", exc_info=True)

    def start(self):
        self.running = True
        logger.info(f"Sync Engine Started at {time.ctime()}.")
        self._loop()

    def stop(self):
        self.running = False

    def _loop(self):
        if self.drive_svc: self._pull_metadata()
        
        while self.running:
            task = self._get_next_retryable_action()
            if task:
                try:
                    self._process_task(task)
                    self.db.complete_action(task['action_id'])
                except Exception as e:
                    logger.error(f"Task {task['action_type']} (ID: {task['action_id']}) failed: {e}", exc_info=True)
                    self.db.fail_action(task['action_id'], task['target_id'], str(e))
            else:
                time.sleep(1)

    def _get_next_retryable_action(self):
        """
        Retrieves the next pending or retryable failed action.
        Failed actions are eligible for retry after an exponential backoff period.
        """
        now = int(time.time())
        conn = self.db.get_conn()

        # Try to get a pending action first (FIFO)
        row = conn.execute("""
            SELECT * FROM actions 
            WHERE status = 'pending' 
            ORDER BY created_at ASC 
            LIMIT 1
        """).fetchone()
        if row:
            # Mark as processing
            self.db.execute("UPDATE actions SET status = 'processing' WHERE action_id = ?", (row['action_id'],))
            return dict(row)

        # If no pending actions, look for failed actions eligible for retry
        # Calculate backoff dynamically
        row = conn.execute(f"""
            SELECT *, 
                   (created_at + ({BASE_BACKOFF_SECONDS} * POWER(2, retry_count))) as next_retry_time
            FROM actions 
            WHERE status = 'failed' AND next_retry_time <= ?
            ORDER BY next_retry_time ASC, created_at ASC 
            LIMIT 1
        """, (now,)).fetchone()
        
        if row:
            # Mark as processing
            self.db.execute("UPDATE actions SET status = 'processing' WHERE action_id = ?", (row['action_id'],))
            return dict(row)
        
        return None

    def _process_task(self, task):
        obj_id = task['target_id']
        action = task['action_type']
        direction = task['direction']
        dest = task['destination']
        
        obj = OrchardObject.load(self.db, obj_id)
        
        # Handle 'list_children' or generic pull actions that don't target a specific object 
        # or where object might be missing (e.g. initial pull)
        if action == 'list_children' and obj_id == 'drive_root':
             self._pull_drive_folder(CLOUD_DOCS_ZONE_ID_ROOT, 'drive_root')
             return

        if not obj:
            logger.warning(f"Object (ID: {obj_id}) not found for action '{action}' (task_id: {task['action_id']}). Skipping.")
            return

        if direction == 'push':
            if action == 'upload':
                self._handle_upload(obj)
            elif action == 'update_content':
                self._handle_update_content(obj)
            elif action == 'rename':
                self._handle_rename(obj, dest)
            elif action == 'move':
                self._handle_move(obj, dest)
            elif action == 'delete':
                self._handle_delete(obj)
        elif direction == 'pull':
            if action == 'download':
                self._handle_download(obj)
            elif action == 'ensure_latest':
                self._handle_ensure_latest(obj)

    def _handle_delete(self, obj):
        if not obj.cloud_id: return 
        logger.info(f"Deleting object '{obj.name}' (ID: {obj.id}, CloudID: {obj.cloud_id}) from cloud.")
        self.drive_svc.delete_item(obj.cloud_id, obj.etag)
        # Cleanup DB
        self.db.execute("DELETE FROM objects WHERE id=?", (obj.id,))
        self.db.execute("DELETE FROM drive_cache WHERE object_id=?", (obj.id,))

    def _handle_upload(self, obj):
        # Resolve parent cloud ID
        parent_row = self.db.fetchone("SELECT cloud_id FROM objects WHERE id=?", (obj.parent_id,))
        parent_cloud_id = parent_row['cloud_id'] if parent_row else None
        if obj.parent_id == 'drive_root': parent_cloud_id = CLOUD_DOCS_ZONE_ID_ROOT
        
        if not parent_cloud_id:
            raise Exception(f"Parent object '{obj.parent_id}' has no cloud ID for upload.")

        full_name = obj.name
        if obj.extension: full_name += f".{obj.extension}"

        if isinstance(obj, DriveFile):
            local_cache_path = obj.get_local_full_path()
            if not os.path.exists(local_cache_path):
                # Check if it was deleted in the meantime (e.g., temp file swap)
                fresh_row = self.db.fetchone("SELECT deleted FROM objects WHERE id=?", (obj.id,))
                if fresh_row and fresh_row['deleted']:
                    logger.warning(f"Skipping upload for deleted file '{full_name}' (ID: {obj.id}).")
                    return
                # Also check if it's completely gone from DB?
                if not fresh_row:
                    logger.warning(f"Skipping upload for non-existent object '{full_name}' (ID: {obj.id}).")
                    return
                    
                raise FileNotFoundError(f"Local file not found: {local_cache_path}")

            logger.info(f"Uploading new file '{full_name}' (ID: {obj.id}) to parent '{obj.parent_id}' (CloudID: {parent_cloud_id}).")
            
            # Use a temporary symlink to provide the correct filename to the upload service
            import tempfile
            with tempfile.TemporaryDirectory() as temp_dir:
                symlink_path = os.path.join(temp_dir, full_name)
                os.symlink(local_cache_path, symlink_path)
                
                try:
                    # Capture response to update DB immediately
                    resp = self.drive_svc.upload_file(symlink_path, parent_cloud_id)
                    # Extract metadata from response
                    new_cloud_id = resp.get('document_id')
                    new_etag = resp.get('etag')
                    new_name = resp.get('name')
                    new_ext = resp.get('extension')
                    new_size = resp.get('size')
                    
                    if new_cloud_id:
                        # Update DB with new metadata, including name/ext if cloud changed it
                        self.db.execute("""
                            UPDATE objects 
                            SET cloud_id=?, etag=?, size=COALESCE(?, size), name=COALESCE(?, name), extension=COALESCE(?, extension), missing_from_cloud=0, dirty=0, sync_state=?, last_synced=? 
                            WHERE id=?
                        """, (new_cloud_id, new_etag, new_size, new_name, new_ext, SYNC_STATE_SYNCHRONIZED, int(time.time()), obj.id))
                        logger.info(f"Upload finalized. DB updated with CloudID: {new_cloud_id}")
                    else:
                        self._mark_synced(obj) # Fallback

                except Exception as e:
                    # Handle 412 Conflict (Precondition Failed) by overwriting
                    is_conflict = False
                    
                    # Check for 412 in the exception message or its cause
                    if "412" in str(e) or "Precondition Failed" in str(e):
                        is_conflict = True
                    elif hasattr(e, '__cause__') and e.__cause__ and ("412" in str(e.__cause__) or "Precondition Failed" in str(e.__cause__)):
                        is_conflict = True
                    elif hasattr(e, 'args') and len(e.args) > 0 and isinstance(e.args[0], Exception) and "412" in str(e.args[0]):
                         is_conflict = True
                    
                    if is_conflict:
                        logger.warning(f"Upload conflict (412) for '{full_name}'. Attempting overwrite...")
                        children = self.drive_svc.list_directory(parent_cloud_id)
                        
                        # Debug log
                        logger.debug(f"Searching for '{full_name}' or '{obj.name}' in {[c.get('name') for c in children]}")

                        # Match against full_name OR base name
                        conflict_item = next((i for i in children if i.get('name') == full_name or i.get('name') == obj.name), None)
                        
                        if conflict_item:
                            c_id = conflict_item.get('docwsid', conflict_item.get('drivewsid'))
                            c_etag = conflict_item.get('etag')
                            logger.info(f"Deleting conflicting remote item {c_id} to allow upload.")
                            self.drive_svc.delete_item(c_id, c_etag)
                            
                            # Retry upload using the symlink path
                            resp = self.drive_svc.upload_file(symlink_path, parent_cloud_id)
                            new_cloud_id = resp.get('document_id')
                            new_etag = resp.get('etag')
                            new_name = resp.get('name')
                            new_ext = resp.get('extension')
                            new_size = resp.get('size')

                            if new_cloud_id:
                                self.db.execute("""
                                    UPDATE objects 
                                    SET cloud_id=?, etag=?, size=COALESCE(?, size), name=COALESCE(?, name), extension=COALESCE(?, extension), missing_from_cloud=0, dirty=0, sync_state=?, last_synced=? 
                                    WHERE id=?
                                """, (new_cloud_id, new_etag, new_size, new_name, new_ext, SYNC_STATE_SYNCHRONIZED, int(time.time()), obj.id))
                            else:
                                self._mark_synced(obj)
                        else:
                            raise e
                    else:
                        raise e

        elif isinstance(obj, DriveFolder):
            logger.info(f"Creating folder '{full_name}' (ID: {obj.id}) in parent '{obj.parent_id}' (CloudID: {parent_cloud_id}).")
            self.drive_svc.create_folder(parent_cloud_id, full_name)
            # Enqueue pull to refresh ID for folders
            self.db.enqueue_action(obj.parent_id, 'list_children', 'pull')
            self._mark_synced(obj)

    def _handle_update_content(self, obj):
        if not isinstance(obj, DriveFile): return
        if not obj.cloud_id: 
            # If no cloud ID, treat as upload
            return self._handle_upload(obj)

        logger.info(f"Updating content for object '{obj.name}' (ID: {obj.id}, CloudID: {obj.cloud_id}).")
        
        # Determine parent cloud ID
        parent_cloud_id = obj.cloud_parent_id
        if not parent_cloud_id:
             # Fallback: resolve from parent object
             parent_row = self.db.fetchone("SELECT cloud_id FROM objects WHERE id=?", (obj.parent_id,))
             parent_cloud_id = parent_row['cloud_id'] if parent_row else None
             if obj.parent_id == 'drive_root': parent_cloud_id = CLOUD_DOCS_ZONE_ID_ROOT

        # Fetch meta for fresh etag
        meta = self.drive_svc.get_item_metadata(obj.cloud_id, parent_id=parent_cloud_id)
        if not meta: 
            logger.warning(f"Object {obj.id} (CloudID: {obj.cloud_id}) missing from cloud during update_content. Marking as missing.")
            self.db.execute("UPDATE objects SET missing_from_cloud=1 WHERE id=?", (obj.id,))
            return

        full_name = obj.name
        if obj.extension: full_name += f".{obj.extension}"
        parent_id = meta.get('parentId')

        # Delete Old -> Upload New
        self.drive_svc.delete_item(obj.cloud_id, meta['etag'])
        
        # Use temporary symlink for upload to correct name
        import tempfile
        import shutil
        with tempfile.TemporaryDirectory() as temp_dir:
            symlink_path = os.path.join(temp_dir, full_name)
            os.symlink(obj.get_local_full_path(), symlink_path)
            
            resp = self.drive_svc.upload_file(symlink_path, parent_id)
            
            new_cloud_id = resp.get('document_id')
            new_etag = resp.get('etag')
            new_name = resp.get('name')
            new_ext = resp.get('extension')
            new_size = resp.get('size')

            if new_cloud_id:
                self.db.execute("""
                    UPDATE objects 
                    SET cloud_id=?, etag=?, size=COALESCE(?, size), name=COALESCE(?, name), extension=COALESCE(?, extension), missing_from_cloud=0, dirty=0, sync_state=?, last_synced=? 
                    WHERE id=?
                """, (new_cloud_id, new_etag, new_size, new_name, new_ext, SYNC_STATE_SYNCHRONIZED, int(time.time()), obj.id))
                logger.info(f"Update finalized. DB updated with new CloudID: {new_cloud_id}")
            else:
                self.db.enqueue_action(obj.parent_id, 'list_children', 'pull')

    def _handle_rename(self, obj, new_name):
        if not obj.cloud_id: return
        
        # Determine parent cloud ID (Rename happens within the same parent)
        parent_cloud_id = obj.cloud_parent_id
        if not parent_cloud_id:
             # Fallback
             parent_row = self.db.fetchone("SELECT cloud_id FROM objects WHERE id=?", (obj.parent_id,))
             parent_cloud_id = parent_row['cloud_id'] if parent_row else None
             if obj.parent_id == 'drive_root': parent_cloud_id = CLOUD_DOCS_ZONE_ID_ROOT
        
        if not parent_cloud_id:
            raise Exception(f"Parent object '{obj.parent_id}' has no cloud ID for renaming object '{obj.name}'.")

        meta = self.drive_svc.get_item_metadata(obj.cloud_id, parent_id=parent_cloud_id)
        if meta is None: 
            logger.warning(f"Object {obj.id} (CloudID: {obj.cloud_id}) missing from cloud during rename. Marking as missing.")
            self.db.execute("UPDATE objects SET missing_from_cloud=1 WHERE id=?", (obj.id,))
            return
        
        logger.info(f"Renaming object '{obj.name}' (ID: {obj.id}, CloudID: {obj.cloud_id}) to '{new_name}'.")
        self.drive_svc.rename_item(obj.cloud_id, meta['etag'], new_name)
        
        # Update local object's name and commit to DB
        obj.name = new_name
        self.db.execute("UPDATE objects SET name=? WHERE id=?", (new_name, obj.id))

        self._mark_synced(obj)

    def _handle_move(self, obj, new_parent_id):
        if not obj.cloud_id: return
        
        logger.debug(f"Attempting to move object. Obj ID: {obj.id}, CloudID: {obj.cloud_id}, Local Parent ID: {obj.parent_id}, New Parent ID: {new_parent_id}")

        original_parent_cloud_id = obj.cloud_parent_id
        
        if not original_parent_cloud_id:
             # Fallback to metadata if cloud_parent_id is not set (legacy or pre-sync)
             task_metadata = json.loads(self.db.fetchone("SELECT metadata FROM actions WHERE target_id = ? AND action_type = 'move' ORDER BY created_at DESC LIMIT 1", (obj.id,))['metadata'])
             original_parent_local_id = task_metadata.get('original_parent_id')

             if not original_parent_local_id:
                 raise Exception(f"Original parent ID not found in metadata for move task of object '{obj.name}' (ID: {obj.id}).")
             
             logger.debug(f"Original parent local ID from task metadata: {original_parent_local_id}")
             
             original_parent_row = self.db.fetchone("SELECT cloud_id FROM objects WHERE id=?", (original_parent_local_id,))
             original_parent_cloud_id = original_parent_row['cloud_id'] if original_parent_row else None
             if original_parent_local_id == 'drive_root': original_parent_cloud_id = CLOUD_DOCS_ZONE_ID_ROOT

        if not original_parent_cloud_id:
             raise Exception(f"Original parent object has no cloud ID for moving object '{obj.name}'.")
             
        logger.debug(f"Resolved Original Parent Cloud ID for metadata retrieval: {original_parent_cloud_id}")

        meta = self.drive_svc.get_item_metadata(obj.cloud_id, parent_id=original_parent_cloud_id)
        if meta is None: 
            logger.warning(f"Object {obj.id} (CloudID: {obj.cloud_id}) missing from cloud during move. Marking as missing.")
            self.db.execute("UPDATE objects SET missing_from_cloud=1 WHERE id=?", (obj.id,))
            return

        # Resolve target parent cloud ID
        target_row = self.db.fetchone("SELECT cloud_id FROM objects WHERE id=?", (new_parent_id,))
        target_cloud_id = target_row['cloud_id'] if target_row else None
        if new_parent_id == 'drive_root': target_cloud_id = CLOUD_DOCS_ZONE_ID_ROOT
        
        if not target_cloud_id: raise Exception(f"Target parent cloud ID not found for parent '{new_parent_id}'.")
        
        logger.info(f"Moving object '{obj.name}' (ID: {obj.id}, CloudID: {obj.cloud_id}) from parent CloudID: '{original_parent_cloud_id}' to new parent CloudID: '{target_cloud_id}'.")
        self.drive_svc.move_item(obj.cloud_id, meta['etag'], target_cloud_id)
        
        # Update local object's parent_id (already done by FS usually, but good to ensure) 
        # AND update cloud_parent_id to reflect the move!
        self.db.execute("UPDATE objects SET parent_id=?, cloud_parent_id=? WHERE id=?", (new_parent_id, target_cloud_id, obj.id))

        self._mark_synced(obj)

    def _handle_download(self, obj):
        if not isinstance(obj, DriveFile) or not obj.cloud_id: return
        logger.info(f"Downloading object '{obj.name}' (ID: {obj.id}, CloudID: {obj.cloud_id}).")
        self.drive_svc.download_file(obj.cloud_id, local_path=obj.get_local_full_path())
        obj.present_locally = 1
        obj.size = os.path.getsize(obj.get_local_full_path())
        self._mark_synced(obj)

    def _handle_ensure_latest(self, obj):
        if not isinstance(obj, DriveFile) or not obj.cloud_id: return
        
        logger.info(f"Ensuring latest version for '{obj.name}' (ID: {obj.id})...")
        
        # 1. Check Metadata
        # Resolve parent cloud ID for reliable lookup
        parent_row = self.db.fetchone("SELECT cloud_id FROM objects WHERE id=?", (obj.parent_id,))
        parent_cloud_id = parent_row['cloud_id'] if parent_row else None
        if obj.parent_id == 'drive_root': parent_cloud_id = CLOUD_DOCS_ZONE_ID_ROOT
        
        # Fallback to obj.cloud_parent_id if DB lookup failed (though DB should have it if populated)
        if not parent_cloud_id and obj.cloud_parent_id:
            parent_cloud_id = obj.cloud_parent_id

        meta = self.drive_svc.get_item_metadata(obj.cloud_id, parent_id=parent_cloud_id)
        
        if not meta:
            logger.warning(f"Object {obj.id} missing on cloud during ensure_latest.")
            self.db.execute("UPDATE objects SET missing_from_cloud=1 WHERE id=?", (obj.id,))
            return

        cloud_etag = meta.get('etag')
        
        # 2. Compare with Local
        needs_download = False
        if not obj.present_locally:
            needs_download = True
            logger.info(f"File '{obj.name}' not present locally. Downloading...")
        elif obj.etag != cloud_etag:
            needs_download = True
            logger.info(f"File '{obj.name}' outdated (Local Etag: {obj.etag} vs Cloud: {cloud_etag}). Downloading...")
        
        # 3. Download if needed
        if needs_download:
            try:
                self.drive_svc.download_file(obj.cloud_id, local_path=obj.get_local_full_path())
                
                # Update DB with new metadata and state
                new_size = os.path.getsize(obj.get_local_full_path())
                
                # Retry loop for DB update
                for i in range(5):
                    try:
                        self.db.execute("""
                            UPDATE objects 
                            SET etag=?, size=?, sync_state=?, last_synced=?, missing_from_cloud=0
                            WHERE id=?
                        """, (cloud_etag, new_size, SYNC_STATE_SYNCHRONIZED, int(time.time()), obj.id))
                        
                        self.db.execute("""
                            UPDATE drive_cache 
                            SET present_locally=1, size=? 
                            WHERE object_id=?
                        """, (new_size, obj.id))
                        break # Success
                    except Exception as e:
                        if "locked" in str(e) and i < 4:
                            time.sleep(0.5)
                            continue
                        raise e
            except Exception as e:
                logger.error(f"Failed to download/update '{obj.name}': {e}")
                raise e
        else:
            logger.info(f"File '{obj.name}' is already up to date.")
            # Just mark synced to be sure
            self._mark_synced(obj)

    def _mark_synced(self, obj):
        now = int(time.time())
        self.db.execute("UPDATE objects SET dirty=0, sync_state=?, last_synced=? WHERE id=?", (SYNC_STATE_SYNCHRONIZED, now, obj.id))

    def _pull_metadata(self):
        if self.drive_svc:
            logger.info("Pulling full Drive metadata...")
            self.db.enqueue_action('drive_root', 'list_children', 'pull')

    def _pull_drive_folder(self, cloud_id, local_parent_id):
        try:
            items = self.drive_svc.list_directory(cloud_id)
        except Exception as e:
            logger.error(f"Failed to list cloud folder (CloudID: {cloud_id}): {e}", exc_info=True)
            return

        current_cloud_ids = set()

        for item in items:
            c_id = item.get('docwsid') if item.get('type') == 'FILE' else item.get('drivewsid')
            current_cloud_ids.add(c_id)
            
            raw_name = item.get('name')
            ext = item.get('extension')
            
            name = raw_name
            if ext and raw_name.endswith(f".{ext}"):
                name = raw_name[:-len(ext)-1]
            
            etag = item.get('etag')
            size = item.get('size', 0)
            
            row = self.db.fetchone("SELECT * FROM objects WHERE cloud_id=?", (c_id,))
            
            if not row:
                new_id = f"{item['type'].lower()}-{os.urandom(8).hex()}"
                self.db.execute("""
                    INSERT INTO objects (id, type, name, parent_id, cloud_id, cloud_parent_id, etag, size, extension, sync_state)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (new_id, item['type'].lower(), name, local_parent_id, c_id, cloud_id, etag, size, ext, SYNC_STATE_SYNCHRONIZED))
                
                if item['type'] == 'FILE':
                    self.db.execute("INSERT INTO drive_cache (object_id, size, present_locally) VALUES (?, ?, 0)", (new_id, size))
                elif item['type'] == 'FOLDER':
                    self.db.execute("INSERT INTO drive_cache (object_id, size, present_locally) VALUES (?, 0, 0)", (new_id,))
                    self._pull_drive_folder(c_id, new_id)
            else:
                if row['etag'] != etag:
                    self.db.execute("UPDATE objects SET etag=?, name=?, size=?, extension=?, parent_id=?, cloud_parent_id=?, missing_from_cloud=0 WHERE id=?", 
                                    (etag, name, size, ext, local_parent_id, cloud_id, row['id']))
                    
                    # Auto-update cached content if outdated
                    if item['type'] == 'FILE':
                        cache_row = self.db.fetchone("SELECT present_locally FROM drive_cache WHERE object_id=?", (row['id'],))
                        if cache_row and cache_row['present_locally']:
                            logger.info(f"Queuing auto-update for cached file '{name}' (ID: {row['id']})")
                            self.db.enqueue_action(row['id'], 'download', 'pull')

                    if item['type'] == 'FOLDER':
                        self._pull_drive_folder(c_id, row['id'])
                elif row['missing_from_cloud']:
                     # If it was marked missing but we found it, clear the flag
                     self.db.execute("UPDATE objects SET missing_from_cloud=0 WHERE id=?", (row['id'],))

        db_children = self.db.fetchall("SELECT id, cloud_id, dirty, missing_from_cloud FROM objects WHERE parent_id=? AND cloud_id IS NOT NULL", (local_parent_id,))
        for child in db_children:
            if child['cloud_id'] not in current_cloud_ids:
                logger.info(f"Detected remote deletion of {child['id']}")
                is_missing = child['missing_from_cloud']
                if child['dirty'] == 1 and not is_missing:
                    logger.warning(f"Conflict detected for {child['id']}: Remote deletion conflicts with local changes.")
                    self.db.execute("UPDATE objects SET sync_state=? WHERE id=?", (SYNC_STATE_CONFLICT, child['id'],))
                else:
                    self.db.execute("UPDATE objects SET deleted=1, sync_state=? WHERE id=?", (SYNC_STATE_SYNCHRONIZED, child['id'],))