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
        self.fd = 0 
        logger.info(f"OrchardFS initialized. DB: {db_path}")
        os.makedirs(ORCHARD_CACHE_DIR, exist_ok=True)

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
                return None
            
            child_id = row['id']
            current_obj = OrchardObject.load(self.db, child_id)
            
            current_path = f"{current_path}/{part}"
            self.path_to_id[current_path] = child_id
            
        return current_obj

    # --- FUSE Operations ---

    def getattr(self, path, fh=None):
        obj = self._resolve(path)
        if not obj: raise FuseOSError(errno.ENOENT)
        
        attrs = {
            'st_uid': os.getuid(), 'st_gid': os.getgid(),
            'st_atime': obj.local.modified_at or int(time.time()),
            'st_mtime': obj.local.modified_at or int(time.time()),
            'st_ctime': obj.local.modified_at or int(time.time()),
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
        obj = self._resolve(path)
        if not obj or obj.type != 'folder': raise FuseOSError(errno.ENOTDIR)

        # Stale Check: If we haven't synced this folder recently, ask for a pull
        last_synced = getattr(obj, 'last_synced', 0) # This needs to be in DB/OrchardObject if used
        if (int(time.time()) - last_synced) > 60:
             # Only queue if not root (root syncs on start)
             if obj.id != 'root': 
                self.db.enqueue_action(obj.id, 'list_children', 'pull')

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
            # Optimistic Open
            if obj.local.present:
                 self.db.enqueue_action(obj.id, 'ensure_latest', 'pull', priority=1)
            obj.local.open_count += 1
            obj.update_cache_entry()
        self.fd += 1
        return self.fd

    def create(self, path, mode, fi=None):
        parent_path, name = os.path.split(path)
        parent_obj = self._resolve(parent_path)
        if not parent_obj or parent_obj.type != 'folder': raise FuseOSError(errno.ENOENT)

        new_obj = DriveFile.create_new_file(self.db, parent_obj.id, name)
        # We don't queue upload here immediately. We wait for release() to capture content.
        # But we can queue a 'touch' or empty upload if needed.
        # For coalescing safety, queuing upload now is fine, release will update metadata.
        self.db.enqueue_action(new_obj.id, 'upload', 'push', metadata={'name': name})
        
        self.fd += 1
        return self.fd

    def read(self, path, size, offset, fh):
        obj = self._resolve(path)
        if not isinstance(obj, DriveFile): raise FuseOSError(errno.EISDIR)

        if not obj.local.present:
            _, _, pid = fuse.fuse_get_context()
            if self._is_blacklisted_process(pid): raise FuseOSError(errno.EACCES)
            
            self.db.enqueue_action(obj.id, 'ensure_latest', 'pull', priority=1)
            # Simple spin-wait for demo (Production should use cond vars)
            for _ in range(60): 
                row = self.db.fetchone("SELECT present_locally FROM drive_cache WHERE object_id=?", (obj.id,))
                if row and row['present_locally']: break
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
        obj = self._resolve(path)
        if not isinstance(obj, DriveFile): return 0
        
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

def mount_daemon(db_path, mount_point):
    if not os.path.exists(mount_point): os.makedirs(mount_point)
    FUSE(OrchardFS(db_path), mount_point, foreground=True)