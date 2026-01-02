import errno
import logging
import os
import stat
import time
import hashlib
import json
import fuse 
from fuse import FUSE, FuseOSError, Operations

from src.db.orchardDB import get_db, OrchardDB
from src.objects.base import OrchardObject
from src.objects.drive import DriveFile, DriveFolder, ORCHARD_CACHE_DIR

logger = logging.getLogger(__name__)

CHUNK_SIZE = 8 * 1024 * 1024
PARTIAL_THRESHOLD = 32 * 1024 * 1024

IGNORED_PROCESSES = [
    'nautilus', 'nemo', 'caja', 'thunar', 'dolphin', 'konqueror', 'pcmanfm',
    'tracker-miner-f', 'tracker-extract', 'baloo_file', 'updatedb', 'locate',
    'gnome-shell', 'systemd-user', 'ffmpeg', 'ffprobe', 'totem', 
    'evince-thumbnailer', 'gstreamer', 'gst-launch', 'xdg-desktop-portal',
    'gnome-desktop-thumbnailer', 'tumbler', 'ffmpegthumbnailer',
    'glycin-thumbnailer', 'xreader-thumbnailer', 'gdk-pixbuf-thumbnailer',
    'mate-thumbnailer'
]

class OrchardFS(Operations):
    def __init__(self, db_path: str):
        self.db: OrchardDB = get_db(db_path)
        # Initialize root mapping
        self.path_to_id = {'/': 'root', '/Drive': 'drive_root'} 
        self.id_to_path = {'root': '/', 'drive_root': '/Drive'}
        self.handle_map = {} # fd -> object_id
        self.fd = 0 
        logger.info(f"OrchardFS initialized. DB: {db_path}")
        os.makedirs(ORCHARD_CACHE_DIR, exist_ok=True)
        
        # Reset open counts on startup (crash recovery)
        self.db.execute("UPDATE drive_cache SET open_count = 0")

    def _calculate_hash(self, path):
        if not os.path.exists(path): return None
        sha256 = hashlib.sha256()
        with open(path, 'rb') as f:
            while True:
                data = f.read(65536)
                if not data: break
                sha256.update(data)
        return sha256.hexdigest()

    def _get_process_name(self, pid):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                return f.read().replace(b'\x00', b' ').decode('utf-8', errors='ignore').strip()
        except Exception: return None

    def _is_blacklisted_process(self, pid):
        cmdline = self._get_process_name(pid)
        if not cmdline: return False
        for proc in IGNORED_PROCESSES:
            if proc in os.path.basename(cmdline.split(' ')[0]) or proc in cmdline:
                return True
        return False

    def _resolve(self, path: str) -> OrchardObject | None:
        """
        Resolves a filesystem path (e.g., /Drive/Documents/file.txt) to a DB Object.
        """
        # logger.debug(f"Resolving path: {path}")

        # 1. Quick Cache Hit
        if path == '/': return OrchardObject.load(self.db, 'root')
        if path in self.path_to_id:
             obj = OrchardObject.load(self.db, self.path_to_id[path])
             if obj and not getattr(obj, 'deleted', 0): return obj
             else: 
                 # Cache is stale or object deleted
                 del self.path_to_id[path]

        # 2. Iterative Resolution (Traverse from Root)
        parts = [p for p in path.strip('/').split('/') if p]
        if not parts: return OrchardObject.load(self.db, 'root')

        # Start at root
        if parts[0] == 'Drive':
            current_obj = OrchardObject.load(self.db, 'drive_root')
            current_path = '/Drive'
            start_idx = 1
        else:
            current_obj = OrchardObject.load(self.db, 'root')
            current_path = ''
            start_idx = 0

        if not current_obj: return None
        self.path_to_id[current_path or '/'] = current_obj.id

        # Traverse remaining parts
        for i in range(start_idx, len(parts)):
            part = parts[i]
            
            # Find child by name in DB, ensuring it is NOT deleted
            # Uses COALESCE to safely handle NULL extensions
            row = self.db.fetchone("""
                SELECT id FROM objects 
                WHERE parent_id = ? 
                AND deleted = 0
                AND (
                    name = ? 
                    OR (name || '.' || COALESCE(extension, '')) = ?
                    OR (name || '.' || COALESCE(extension, '')) = ? || '.'
                )
            """, (current_obj.id, part, part, part))
            
            if not row:
                logger.debug(f"Failed to resolve '{part}' in {current_obj.id} ({current_path})")
                return None
            
            child_id = row['id']
            current_obj = OrchardObject.load(self.db, child_id)
            
            current_path = f"{current_path}/{part}"
            self.path_to_id[current_path] = child_id
            
        return current_obj

    # --- FUSE Operations ---

    def getattr(self, path, fh=None):
        # logger.debug(f"getattr: {path}")
        obj = self._resolve(path)
        if not obj: raise FuseOSError(errno.ENOENT)
        
        attrs = {
            'st_uid': os.getuid(), 'st_gid': os.getgid(),
            'st_atime': obj.local.last_accessed or int(time.time()),
            'st_mtime': obj.local.modified_at or int(time.time()),
            'st_ctime': max(obj.local.modified_at or 0, obj.local.last_accessed or 0) or int(time.time()),
            'st_nlink': 2 if obj.type == 'folder' else 1
        }
        if obj.type == 'folder':
            attrs['st_mode'] = (stat.S_IFDIR | 0o755)
            attrs['st_size'] = 4096
        else:
            attrs['st_mode'] = (stat.S_IFREG | 0o644)
            attrs['st_size'] = obj.local.size
        return attrs

    def readdir(self, path, fh):
        logger.info(f"readdir: {path}")
        obj = self._resolve(path)
        if not obj or obj.type != 'folder': raise FuseOSError(errno.ENOTDIR)

        # Stale Check: If we haven't synced this folder recently, ask for a pull
        last_synced = getattr(obj, 'last_synced', 0) # This needs to be in DB/OrchardObject if used
        
        logger.info(f"Checking stale for {path} (ID: {obj.id}). Last Synced: {last_synced}. Now: {int(time.time())}")
        
        if (int(time.time()) - last_synced) > 60:
             # Only queue if not root (root syncs on start)
             if obj.id != 'root': 
                logger.info(f"Queueing list_children for {obj.id}")
                # High priority (20) to jump ahead of background tasks
                self.db.enqueue_action(obj.id, 'list_children', 'pull', priority=20) 
                
                # If never synced (0), BLOCK until data arrives
                if last_synced == 0:
                     logger.info(f"Blocking readdir for {path} until sync completes...")
                     for _ in range(20): # Wait up to 10s
                         time.sleep(0.5)
                         # Reload obj to check last_synced
                         updated_obj = OrchardObject.load(self.db, obj.id)
                         if getattr(updated_obj, 'last_synced', 0) > 0:
                             logger.info(f"Sync complete for {path}. Proceeding.")
                             break

        dirents = ['.', '..']
        
        # We need to query children from DB now, EXCLUDING deleted items
        children = self.db.fetchall("""
            SELECT name, extension, type 
            FROM objects 
            WHERE parent_id = ? AND deleted = 0
        """, (obj.id,))
        
        for child in children:
            name = child['name']
            if child['type'] == 'file' and child['extension']:
                name = f"{name}.{child['extension']}"
            dirents.append(name)
            
        for r in dirents: yield r

    def open(self, path, flags):
        obj = self._resolve(path)
        if not obj: raise FuseOSError(errno.ENOENT)
        if obj.type == 'folder': raise FuseOSError(errno.EISDIR)
        
        if isinstance(obj, DriveFile):
            # Hybrid Strategy: Partial vs Full
            # present=0 (Missing), present=1 (Full), present=2 (Partial)
            if not obj.local.present:
                 _, _, pid = fuse.fuse_get_context()
                 # If thumbnailer, skip download (prevent accidental trigger)
                 if self._is_blacklisted_process(pid): 
                     pass
                 elif obj.local.size < PARTIAL_THRESHOLD:
                      # Small File -> Full Download
                      self.db.enqueue_action(obj.id, 'ensure_latest', 'pull', priority=10)
                 else:
                      # Large File -> Sparse Init
                      obj.create_sparse_placeholder()
            
            obj.local.open_count += 1
            obj.update_cache_entry()
        
        self.fd += 1
        self.handle_map[self.fd] = obj.id
        return self.fd

    def create(self, path, mode, fi=None):
        parent_path, name = os.path.split(path)
        
        # Filter temp files
        if name.startswith('.goutputstream') or name.startswith('.Trash') or name.startswith('._'):
            # Create local placeholder but DO NOT enqueue sync action yet
            parent_obj = self._resolve(parent_path)
            if not parent_obj or parent_obj.type != 'folder': raise FuseOSError(errno.ENOENT)
            
            # We still need a DB object to track the file handle
            new_obj = DriveFile.create_new_file(self.db, parent_obj.id, name)
            self.fd += 1
            self.handle_map[self.fd] = new_obj.id
            return self.fd

        parent_obj = self._resolve(parent_path)
        if not parent_obj or parent_obj.type != 'folder': raise FuseOSError(errno.ENOENT)

        new_obj = DriveFile.create_new_file(self.db, parent_obj.id, name)
        # We don't queue upload here immediately. We wait for release() to capture content.
        # But we can queue a 'touch' or empty upload if needed.
        # For coalescing safety, queuing upload now is fine, release will update metadata.
        self.db.enqueue_action(new_obj.id, 'upload', 'push', metadata={'name': name})
        
        self.fd += 1
        self.handle_map[self.fd] = new_obj.id
        return self.fd

    def read(self, path, size, offset, fh):
        obj = self._resolve(path)
        if not isinstance(obj, DriveFile): raise FuseOSError(errno.EISDIR)

        # Check if we need data (Missing (0) or Partial (2))
        # If present=1, we have everything, skip logic
        if obj.local.present != 1:
            _, _, pid = fuse.fuse_get_context()
            if self._is_blacklisted_process(pid): raise FuseOSError(errno.EACCES)

            # Block-based Logic
            start_chunk = offset // CHUNK_SIZE
            end_chunk = (offset + size - 1) // CHUNK_SIZE
            needed_chunks = range(start_chunk, end_chunk + 1)
            
            present_chunks = self.db.get_present_chunks(obj.id)
            missing = [c for c in needed_chunks if c not in present_chunks]

            if missing:
                # Enqueue actions for missing chunks
                for c in missing:
                    self.db.enqueue_action(
                        obj.id, 'download_chunk', 'pull',
                        metadata={'chunk_index': c}, priority=10
                    )
                
                # Blocking Wait Loop
                # Timeout: 30s
                for _ in range(60): 
                    # Refresh state
                    row = self.db.fetchone("SELECT present_locally FROM drive_cache WHERE object_id=?", (obj.id,))
                    if row and row['present_locally'] == 1: break # Full download completed
                    
                    present_chunks = self.db.get_present_chunks(obj.id)
                    if all(c in present_chunks for c in missing): break
                    
                    time.sleep(0.5)

        try: return obj.read_local(size, offset)
        except: raise FuseOSError(errno.EIO)

    def write(self, path, data, offset, fh):
        obj = self._resolve(path)
        if not isinstance(obj, DriveFile): raise FuseOSError(errno.EISDIR)
        
        if not obj.local.present: obj.create_local_placeholder()
        ret = obj.write_local(data, offset)
        # Note: We removed the enqueue_action here. We do it in release()
        return ret

    def truncate(self, path, length, fh=None):
        obj = self._resolve(path)
        if not isinstance(obj, DriveFile): raise FuseOSError(errno.EIO)
        
        path_loc = obj.get_local_full_path()
        if not os.path.exists(path_loc): obj.create_local_placeholder()
        
        with open(obj.get_local_full_path(), 'r+b') as f:
            f.truncate(length)
        
        obj.local.size = length
        obj.local.dirty = 1
        obj.commit()

    def release(self, path, fh):
        """Called when file is closed. Checks for changes and queues upload."""
        obj_id = self.handle_map.pop(fh, None)
        if obj_id:
            obj = OrchardObject.load(self.db, obj_id)
        else:
            obj = self._resolve(path)

        if not isinstance(obj, DriveFile): return 0
        
        # Decrement Open Count
        if obj.local.open_count > 0:
            obj.local.open_count -= 1
            obj.update_cache_entry()
            if obj.local.dirty: obj.commit() # Only persist if dirty
            
        # Ignore temp files
        if obj.local.name.startswith('.goutputstream') or obj.local.name.startswith('.Trash') or obj.local.name.startswith('._'):
            return 0
            
        # FIX: Do not upload partial files. 
        if obj.local.present == 2:
            return 0
            
        # Optimization: If file was not modified (dirty=0), do not queue upload.
        if not obj.local.dirty:
            return 0

        # FIX: Only upload if file is fully closed by all processes
        if obj.local.open_count > 0:
            logger.info(f"Skipping upload for {obj.id}: File still open (count={obj.local.open_count})")
            return 0

        local_path = obj.get_local_full_path()
        if not os.path.exists(local_path): return 0

        # 1. Calculate Hash & Size
        file_hash = self._calculate_hash(local_path)
        
        # 2. Check Shadow (Optimization for synced files)
        shadow = self.db.get_shadow(obj.id)
        if shadow and shadow['file_hash'] == file_hash:
            return 0 

        # 3. Check Pending Actions (Optimization for offline/syncing files)
        # If we already have a pending upload/update for this file with the same content, skip.
        pending = self.db.fetchall("""
            SELECT metadata FROM actions 
            WHERE target_id = ? AND action_type IN ('upload', 'update_content') 
            AND status IN ('pending', 'processing', 'failed')
            ORDER BY created_at DESC LIMIT 1
        """, (obj.id,))
        
        if pending:
            try:
                meta = json.loads(pending[0]['metadata'])
                if meta.get('file_hash') == file_hash:
                    return 0 # Duplicate action, skip
            except: pass

        # 4. Queue Action
        self.db.enqueue_action(
            obj.id, 'update_content', 'push', 
            metadata={'file_hash': file_hash, 'name': obj.local.name}
        )
        return 0

    def rename(self, old_path, new_path):
        old_parent, old_name = os.path.split(old_path)
        new_parent, new_name = os.path.split(new_path)
        obj = self._resolve(old_path)
        dest_parent = self._resolve(new_parent)
        
        if not obj or not dest_parent: raise FuseOSError(errno.ENOENT)

        original_parent_id = obj.local.parent_id
        is_move = (obj.local.parent_id != dest_parent.id)
        is_rename = (old_name != new_name)

        # Update Local DB immediately for UI responsiveness
        if obj.type == 'file' and '.' in new_name:
            base, ext = new_name.rsplit('.', 1)
            obj.local.name = base
            obj.local.extension = ext
        else:
            obj.local.name = new_name
            obj.local.extension = None
            
        obj.local.parent_id = dest_parent.id
        obj.commit()

        # Enqueue with Metadata for intent safety
        if is_move:
            self.db.enqueue_action(
                obj.id, 'move', 'push', 
                destination=dest_parent.id, 
                metadata={'original_parent_id': original_parent_id}
            )
        if is_rename:
            self.db.enqueue_action(
                obj.id, 'rename', 'push', 
                destination=new_name, 
                metadata={'from_name': old_name, 'to_name': new_name}
            )
            
        if old_path in self.path_to_id: del self.path_to_id[old_path]

    def unlink(self, path):
        obj = self._resolve(path)
        if not obj: raise FuseOSError(errno.ENOENT)
        
        self.db.execute("UPDATE objects SET deleted=1 WHERE id=?", (obj.id,))
        self.db.enqueue_action(obj.id, 'delete', 'push')
        
        if isinstance(obj, DriveFile):
            p = obj.get_local_full_path()
            if os.path.exists(p): os.remove(p)
        if path in self.path_to_id: del self.path_to_id[path]

    def mkdir(self, path, mode):
        parent_path, name = os.path.split(path)
        parent = self._resolve(parent_path)
        if not parent: raise FuseOSError(errno.ENOENT)
        
        new_obj = DriveFolder.create_new_folder(self.db, parent.id, name)
        self.db.enqueue_action(new_obj.id, 'upload', 'push', metadata={'name': name})

    def rmdir(self, path):
        # reuse unlink logic mostly, but check empty
        obj = self._resolve(path)
        if not obj: raise FuseOSError(errno.ENOENT)
        # Check for children manually
        children = self.db.fetchone("SELECT 1 FROM objects WHERE parent_id = ? AND deleted=0 LIMIT 1", (obj.id,))
        if children: raise FuseOSError(errno.ENOTEMPTY)
        self.unlink(path)

    # --- Extended Attributes (Pinning) ---

    def getxattr(self, path, name, position=0):
        obj = self._resolve(path)
        if not obj: raise FuseOSError(errno.ENOENT)
        
        if name == 'user.orchard.pinned':
            row = self.db.fetchone("SELECT pinned FROM drive_cache WHERE object_id=?", (obj.id,))
            if row and row['pinned']: return b'1'
            return b'0'
            
        # Universal Emblems (Dolphin, Thunar)
        if name == 'user.xdg.emblems':
            if obj.type == 'folder': raise FuseOSError(errno.ENODATA)
            
            # 1. Conflict
            if obj.sync_state == 'conflict': return b'emblem-orchard-conflict'
            
            # 2. Local Modification (Dirty)
            if obj.local.dirty: return b'emblem-orchard-modified'
            
            row = self.db.fetchone("SELECT present_locally FROM drive_cache WHERE object_id=?", (obj.id,))
            state = row['present_locally'] if row else 0
            
            # 3. Content State
            if state == 1: return b'emblem-orchard-local'
            if state == 2: return b'emblem-orchard-partial'
            return b'emblem-orchard-cloud' # Cloud

        if name == 'user.orchard.status':
            if obj.type == 'folder': raise FuseOSError(errno.ENODATA)
            
            if obj.sync_state == 'conflict': return b'conflict'
            if obj.local.dirty: return b'modified'
            
            row = self.db.fetchone("SELECT present_locally FROM drive_cache WHERE object_id=?", (obj.id,))
            state = row['present_locally'] if row else 0
            
            if state == 1: return b'local'
            if state == 2: return b'partial'
            return b'cloud'
        
        # We don't support other xattrs yet
        raise FuseOSError(errno.ENODATA)

    def listxattr(self, path):
        obj = self._resolve(path)
        if not obj: raise FuseOSError(errno.ENOENT)
        return ['user.orchard.pinned', 'user.orchard.status', 'user.xdg.emblems']

    def setxattr(self, path, name, value, options, position=0):
        obj = self._resolve(path)
        if not obj: raise FuseOSError(errno.ENOENT)
        
        if name == 'user.orchard.pinned':
            is_pinned = (value == b'1' or value == b'true')
            val_int = 1 if is_pinned else 0
            
            self.db.execute("""
                INSERT OR IGNORE INTO drive_cache (object_id, pinned) VALUES (?, ?)
            """, (obj.id, val_int))
            self.db.execute("UPDATE drive_cache SET pinned=? WHERE object_id=?", (val_int, obj.id))
            
            if is_pinned:
                # If pinning, ensure we have the full file
                if not obj.local.present or obj.local.present == 2:
                    logger.info(f"Pinning {obj.local.name}: Queuing full download.")
                    self.db.enqueue_action(obj.id, 'ensure_latest', 'pull', priority=5)
            else:
                # Unpinning -> Free Up Space (Evict)
                # SAFETY: Do not evict if dirty!
                if obj.local.dirty:
                    logger.warning(f"Cannot evict {obj.local.name}: File has unsynced changes.")
                else:
                    logger.info(f"Evicting {obj.local.name} (Free Up Space).")
                    path = obj.get_local_full_path()
                    if os.path.exists(path):
                        with open(path, 'wb') as f: pass # Truncate to 0
                    
                    self.db.execute("UPDATE drive_cache SET present_locally=0 WHERE object_id=?", (obj.id,))
                    self.db.execute("DELETE FROM chunk_cache WHERE object_id=?", (obj.id,))
            
            return 0
            
        raise FuseOSError(errno.EOPNOTSUPP)

def mount_daemon(db_path, mount_point):
    if not os.path.exists(mount_point): os.makedirs(mount_point)
    FUSE(OrchardFS(db_path), mount_point, foreground=True)