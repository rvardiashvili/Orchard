import threading
import time
import logging
import os
import json
import uuid
import shutil
import tempfile

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
from src.config.sync_config import MAX_RETRIES, BASE_BACKOFF_SECONDS

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
        now = int(time.time())
        conn = self.db.get_conn()

        # 1. Pending (FIFO)
        row = conn.execute("""
            SELECT * FROM actions 
            WHERE status = 'pending' 
            ORDER BY created_at ASC LIMIT 1
        """).fetchone()
        if row:
            self.db.execute("UPDATE actions SET status = 'processing' WHERE action_id = ?", (row['action_id'],))
            return dict(row)

        # 2. Retryable Failed (Backoff)
        row = conn.execute(f"""
            SELECT *, (created_at + ({BASE_BACKOFF_SECONDS} * POWER(2, retry_count))) as next_retry_time
            FROM actions 
            WHERE status = 'failed' AND next_retry_time <= ?
            ORDER BY next_retry_time ASC, created_at ASC LIMIT 1
        """, (now,)).fetchone()
        
        if row:
            self.db.execute("UPDATE actions SET status = 'processing' WHERE action_id = ?", (row['action_id'],))
            return dict(row)
        
        return None

    def _process_task(self, task):
        obj_id = task['target_id']
        action = task['action_type']
        direction = task['direction']
        dest = task['destination']
        
        metadata = json.loads(task['metadata']) if task['metadata'] else {}
        
        # Root listing special case
        if action == 'list_children' and obj_id == 'drive_root':
             self._pull_drive_folder(CLOUD_DOCS_ZONE_ID_ROOT, 'drive_root')
             return

        obj = OrchardObject.load(self.db, obj_id)
        
        # Subfolder listing special case
        if action == 'list_children' and obj and obj.cloud.id:
             self._pull_drive_folder(obj.cloud.id, obj.id)
             return

        if not obj:
            if action == 'delete':
                self._handle_delete_by_id(obj_id)
                return
            logger.warning(f"Object (ID: {obj_id}) not found for action '{action}'. Skipping.")
            return

        if direction == 'push':
            if action == 'upload':
                self._handle_upload(obj, metadata)
            elif action == 'update_content':
                self._handle_update_content(obj, metadata)
            elif action == 'rename':
                self._handle_rename(obj, dest, metadata)
            elif action == 'move':
                self._handle_move(obj, dest, metadata)
            elif action == 'delete':
                self._handle_delete(obj)
        elif direction == 'pull':
            if action == 'download':
                self._handle_download(obj)
            elif action == 'ensure_latest':
                self._handle_ensure_latest(obj)

    # ----------------------------------------------------------------
    # HANDLERS
    # ----------------------------------------------------------------

    def _pull_drive_folder(self, cloud_id, local_parent_id):
        try:
            items = self.drive_svc.list_directory(cloud_id)
        except Exception as e:
            logger.error(f"Failed to list directory {cloud_id}: {e}")
            return

        for item in items:
            c_id = item.get('docwsid') or item.get('drivewsid')
            if not c_id: continue
            
            etag = item.get('etag')
            name = item.get('name')
            extension = item.get('extension') 
            size = item.get('size', 0)
            item_type = item.get('type', 'FILE').lower()
            if item_type == 'app_library': item_type = 'folder' 

            existing = self.db.fetchone("SELECT * FROM objects WHERE cloud_id=?", (c_id,))

            if existing:
                if existing['dirty']: continue # Conflict check
                
                self.db.execute("""
                    UPDATE objects 
                    SET etag=?, name=?, extension=?, size=?, type=?, cloud_parent_id=?, last_synced=?, missing_from_cloud=0
                    WHERE cloud_id=?
                """, (etag, name, extension, size, item_type, cloud_id, int(time.time()), c_id))
                
                self.db.update_shadow(existing['id'], cloud_id=c_id, parent_id=local_parent_id, etag=etag, name=name, modified_at=int(time.time()))
            else:
                new_id = str(uuid.uuid4())
                self.db.execute("""
                    INSERT INTO objects (id, type, parent_id, name, extension, size, cloud_id, cloud_parent_id, etag, sync_state, last_synced)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    new_id, item_type, local_parent_id, name, extension, size, 
                    c_id, cloud_id, etag, SYNC_STATE_SYNCHRONIZED, int(time.time())
                ))
                self.db.update_shadow(new_id, cloud_id=c_id, parent_id=local_parent_id, name=name, etag=etag, modified_at=int(time.time()))

    def _handle_upload(self, obj, metadata):
        target_name = metadata.get('name', obj.local.name)
        target_hash = metadata.get('file_hash')

        shadow = self.db.get_shadow(obj.id)
        if shadow and target_hash and shadow['file_hash'] == target_hash:
            logger.info(f"Skipping upload for {obj.id}: Shadow hash matches intent.")
            return

        # Resolve Parent Cloud ID
        parent_cloud_id = obj.local.parent_id
        if obj.local.parent_id == 'drive_root': parent_cloud_id = CLOUD_DOCS_ZONE_ID_ROOT
        
        if not parent_cloud_id: raise Exception(f"Parent {obj.local.parent_id} has no cloud ID")

        full_name = target_name
        if obj.local.extension and not target_name.endswith(f".{obj.local.extension}"):
            full_name += f".{obj.local.extension}"

        if isinstance(obj, DriveFile):
            local_cache_path = obj.get_local_full_path()
            if not os.path.exists(local_cache_path): raise FileNotFoundError(local_cache_path)

            logger.info(f"Uploading '{full_name}' (ID: {obj.id}) to CloudID: {parent_cloud_id}")
            
            with tempfile.TemporaryDirectory() as temp_dir:
                symlink_path = os.path.join(temp_dir, full_name)
                os.symlink(local_cache_path, symlink_path)
                
                resp = self.drive_svc.upload_file(symlink_path, parent_cloud_id)
                
                new_cloud_id = resp.get('document_id') or resp.get('docwsid')
                new_etag = resp.get('etag')
                new_size = resp.get('size')
                
                if new_cloud_id:
                    self._update_db_and_shadow(obj, new_cloud_id, new_etag, new_size, target_hash, parent_cloud_id)

        elif isinstance(obj, DriveFolder):
            self.drive_svc.create_folder(parent_cloud_id, full_name)
            self.db.enqueue_action(obj.local.parent_id, 'list_children', 'pull')
            self._mark_synced(obj)

    def _handle_update_content(self, obj, metadata):
        if not obj.cloud.id: return self._handle_upload(obj, metadata)
        
        target_hash = metadata.get('file_hash')
        shadow = self.db.get_shadow(obj.id)
        if shadow and target_hash and shadow['file_hash'] == target_hash:
            return

        logger.info(f"Updating content for {obj.id}")
        
        # Robust Parent Resolution
        parent_id = obj.cloud.parent_id
        if not parent_id:
             if obj.local.parent_id == 'drive_root': parent_id = CLOUD_DOCS_ZONE_ID_ROOT
             else:
                 p_row = self.db.fetchone("SELECT cloud_id FROM objects WHERE id=?", (obj.local.parent_id,))
                 if p_row: parent_id = p_row['cloud_id']

        # Try to delete old version first
        meta = self.drive_svc.get_item_metadata(obj.cloud.id, parent_id=parent_id)
        if meta:
            self.drive_svc.delete_item(obj.cloud.id, meta['etag'])
        
        # Upload new version
        self._handle_upload(obj, metadata)

    def _handle_rename(self, obj, dest, metadata):
        target_name = metadata.get('to_name', dest)
        if not obj.cloud.id: return

        # Robust Parent Resolution for Rename
        parent_id = obj.cloud.parent_id
        if not parent_id:
             if obj.local.parent_id == 'drive_root': 
                 parent_id = CLOUD_DOCS_ZONE_ID_ROOT
             else:
                 # Fallback to local parent lookup
                 p_row = self.db.fetchone("SELECT cloud_id FROM objects WHERE id=?", (obj.local.parent_id,))
                 if p_row: parent_id = p_row['cloud_id']
        print("Parent ID:", parent_id)
        print("Cloud Parent ID:", obj.cloud.parent_id)
        meta = self.drive_svc.get_item_metadata(obj.cloud.id, parent_id=parent_id)
        if not meta: 
            logger.warning(f"Could not find metadata for {obj.id} (CloudID: {obj.cloud.id}, Parent: {parent_id})")
            return 

        logger.info(f"Renaming {obj.id} to {target_name} with etag {meta['etag']}")
        self.drive_svc.rename_item(obj.cloud.id, meta['etag'], target_name)
        logger.info(f"Renamed {obj.id} to {target_name} successfully.")
        
        obj.local.name = target_name
        self.db.execute("UPDATE objects SET name=? WHERE id=?", (target_name, obj.id))
        logger.info(f"Updated DB name for {obj.id} to {target_name}.")
        self.db.update_shadow(obj.id, name=target_name)
        logger.info(f"Updated shadow name for {obj.id} to {target_name}.")
        self._mark_synced(obj)
        logger.info(f"Marked {obj.id} as synced after rename.")

    def _handle_move(self, obj, dest, metadata):
        target_parent_row = self.db.fetchone("SELECT cloud_id FROM objects WHERE id=?", (dest,))
        target_cloud_id = target_parent_row['cloud_id'] if target_parent_row else None
        if dest == 'drive_root': target_cloud_id = CLOUD_DOCS_ZONE_ID_ROOT
        
        if not obj.cloud.id or not target_cloud_id: 
            logger.warning(f"Missing Cloud IDs for move: Obj={obj.cloud.id}, Target={target_cloud_id}")
            return

        original_parent_id_local = metadata.get('original_parent_id')
        original_parent_cloud_id = None

        if original_parent_id_local:
             if original_parent_id_local == 'drive_root': 
                 original_parent_cloud_id = CLOUD_DOCS_ZONE_ID_ROOT
             else:
                 orig_row = self.db.fetchone("SELECT cloud_id FROM objects WHERE id=?", (original_parent_id_local,))
                 if orig_row: original_parent_cloud_id = orig_row['cloud_id']
        
        # Fallback: if metadata didn't have original parent, or lookup failed, try obj.cloud_parent_id
        if not original_parent_cloud_id:
            original_parent_cloud_id = obj.cloud.parent_id

        meta = self.drive_svc.get_item_metadata(obj.cloud.id, parent_id=original_parent_cloud_id)
        if meta:
            self.drive_svc.move_item(obj.cloud.id, meta['etag'], target_cloud_id)
            self.db.execute("UPDATE objects SET parent_id=?, cloud_parent_id=? WHERE id=?", (dest, target_cloud_id, obj.id))
            self.db.update_shadow(obj.id, parent_id=dest)
            self._mark_synced(obj)
        else:
             logger.warning(f"Could not find metadata for move source {obj.id}")

    def _handle_delete(self, obj):
        if not obj.cloud.id: return
        try:
            self.drive_svc.delete_item(obj.cloud.id, obj.cloud.etag) 
        except Exception:
            pass 
        self._cleanup_local(obj.id)

    def _handle_delete_by_id(self, obj_id):
        shadow = self.db.get_shadow(obj_id)
        if shadow and shadow['cloud_id']:
            try:
                self.drive_svc.delete_item(shadow['cloud_id'])
            except: pass
        self._cleanup_local(obj_id)

    def _handle_download(self, obj):
        if not isinstance(obj, DriveFile) or not obj.cloud.id: return
        path = obj.get_local_full_path()
        self.drive_svc.download_file(obj.cloud.id, local_path=path)
        
        import hashlib
        sha = hashlib.sha256()
        with open(path, 'rb') as f:
            while chunk := f.read(8192): sha.update(chunk)
        
        obj.local.present = 1
        obj.local.size = os.path.getsize(path)
        
        # Download doesn't change parent, but we can pass existing one to stay safe
        self._update_db_and_shadow(obj, obj.cloud.id, obj.cloud.etag, obj.local.size, sha.hexdigest())

    def _handle_ensure_latest(self, obj):
        if not obj.cloud.id: return
        
        parent_cloud_id = obj.cloud.parent_id
        
        meta = self.drive_svc.get_item_metadata(obj.cloud.id, parent_id=parent_cloud_id)
        if not meta: return 
        
        cloud_etag = meta.get('etag')
        
        if not obj.local.present or obj.cloud.etag != cloud_etag:
            self._handle_download(obj)
        else:
            self._mark_synced(obj)

    # ----------------------------------------------------------------
    # HELPERS
    # ----------------------------------------------------------------

    def _update_db_and_shadow(self, obj, cloud_id, etag, size, file_hash, cloud_parent_id=None):
        now = int(time.time())
        
        # Construct update query dynamically based on whether cloud_parent_id is provided
        if cloud_parent_id:
            self.db.execute("""
                UPDATE objects 
                SET cloud_id=?, etag=?, size=COALESCE(?, size), missing_from_cloud=0, dirty=0, sync_state=?, last_synced=?, cloud_parent_id=?
                WHERE id=?
            """, (cloud_id, etag, size, SYNC_STATE_SYNCHRONIZED, now, cloud_parent_id, obj.id))
        else:
            self.db.execute("""
                UPDATE objects 
                SET cloud_id=?, etag=?, size=COALESCE(?, size), missing_from_cloud=0, dirty=0, sync_state=?, last_synced=?
                WHERE id=?
            """, (cloud_id, etag, size, SYNC_STATE_SYNCHRONIZED, now, obj.id))
        
        self.db.update_shadow(
            obj.id, cloud_id=cloud_id, etag=etag, file_hash=file_hash, modified_at=now, parent_id=obj.local.parent_id
        )
        
        if isinstance(obj, DriveFile):
            self.db.execute("UPDATE drive_cache SET present_locally=1, size=? WHERE object_id=?", (size, obj.id))

    def _cleanup_local(self, obj_id):
        self.db.execute("DELETE FROM objects WHERE id=?", (obj_id,))
        self.db.execute("DELETE FROM drive_cache WHERE object_id=?", (obj_id,))
        self.db.delete_shadow(obj_id)

    def _mark_synced(self, obj):
        now = int(time.time())
        self.db.execute("UPDATE objects SET dirty=0, sync_state=?, last_synced=? WHERE id=?", (SYNC_STATE_SYNCHRONIZED, now, obj.id))

    def _pull_metadata(self):
        if self.drive_svc:
            self.db.enqueue_action('drive_root', 'list_children', 'pull')