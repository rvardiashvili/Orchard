import errno
import logging
import os
import stat
import time
import fuse # Needed for get_context
from fuse import FUSE, FuseOSError, Operations

from src.db.orchardDB import get_db, OrchardDB
from src.objects.base import OrchardObject
from src.config.sync_states import SYNC_STATE_PENDING_PUSH
from src.objects.drive import DriveFile, DriveFolder, ORCHARD_CACHE_DIR

logger = logging.getLogger(__name__)

# Processes that should NOT trigger a download on read
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
        
        # Path Cache
        self.path_to_id = {
            '/': 'root',
            '/Drive': 'drive_root',
        } 
        self.id_to_path = {
            'root': '/',
            'drive_root': '/Drive',
        }
        self.fd = 0 

        logger.info(f"OrchardFS initialized. DB: {db_path}")
        os.makedirs(ORCHARD_CACHE_DIR, exist_ok=True)

    def _get_process_name(self, pid):
        """Returns the command line of the process or None."""
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b'\x00', b' ').decode('utf-8', errors='ignore').strip()
                return cmdline
        except Exception:
            return None

    def _is_blacklisted_process(self, pid):
        """Checks if the process is in the ignore list for downloads."""
        cmdline = self._get_process_name(pid)
        if not cmdline: return False
        
        parts = cmdline.split(' ')
        exe_name = os.path.basename(parts[0])
        
        # Check against list
        for proc in IGNORED_PROCESSES:
            if proc in exe_name or proc in cmdline:
                return True
        return False

    def _resolve(self, path: str) -> OrchardObject | None:
        if path == '/': return OrchardObject.load(self.db, 'root')
        if path in self.path_to_id:
             obj = OrchardObject.load(self.db, self.path_to_id[path])
             if obj: return obj
             else: del self.path_to_id[path]

        parts = path.strip('/').split('/')
        root_name = parts[0]
        
        # Only Drive is supported
        if root_name == 'Drive': 
            initial_obj_id = 'drive_root'
        else:
            return None 

        current_obj = OrchardObject.load(self.db, initial_obj_id)
        current_path_segments = [root_name]

        for part in parts[1:]:
            if not current_obj or not hasattr(current_obj, 'get_child'):
                return None
            
            child_obj = current_obj.get_child(part)
            if not child_obj: return None
            
            current_obj = child_obj
            current_path_segments.append(part)
            
            full_segment_path = '/' + '/'.join(current_path_segments)
            if full_segment_path not in self.path_to_id:
                self.path_to_id[full_segment_path] = current_obj.id
                self.id_to_path[current_obj.id] = full_segment_path
            
        return current_obj

    def _get_object_attrs(self, obj: OrchardObject) -> dict:
        attrs = {}
        now = int(time.time())

        if obj.type == 'folder': 
            attrs['st_mode'] = (stat.S_IFDIR | 0o755)
            attrs['st_nlink'] = 2
            attrs['st_size'] = 4096
        elif obj.type == 'file':
            attrs['st_mode'] = (stat.S_IFREG | 0o644)
            attrs['st_nlink'] = 1
            attrs['st_size'] = obj.size
        else:
            raise FuseOSError(errno.ENOENT)

        attrs['st_uid'] = os.getuid()
        attrs['st_gid'] = os.getgid()
        attrs['st_atime'] = obj.local_modified_at or now
        attrs['st_mtime'] = obj.local_modified_at or now
        attrs['st_ctime'] = obj.local_modified_at or now

        return attrs

    # --- FUSE Operations ---

    def getattr(self, path, fh=None):
        obj = self._resolve(path)
        if not obj: raise FuseOSError(errno.ENOENT)
        return self._get_object_attrs(obj)

    def readdir(self, path, fh):
        obj = self._resolve(path)
        if not obj or obj.type != 'folder': raise FuseOSError(errno.ENOTDIR)

        # Background Update Check
        now = int(time.time())
        # Threshold: 60 seconds? Or user configurable? Default 60s for now.
        if (now - getattr(obj, 'last_synced', 0)) > 60:
            # Check if already pending to avoid flood
            pending = self.db.fetchone(
                "SELECT 1 FROM actions WHERE target_id=? AND action_type='list_children' AND status IN ('pending', 'processing')", 
                (obj.id,)
            )
            if not pending:
                logger.info(f"Triggering background listing for {path} (Stale)")
                self.db.enqueue_action(obj.id, 'list_children', 'pull')

        dirents = ['.', '..']
        
        # Iterate over child OBJECTS
        for child in obj.list_children():
            name = child.name
            # Reconstruct full filename for FUSE display if extension exists
            if hasattr(child, 'extension') and child.extension:
                name = f"{child.name}.{child.extension}"
            
            dirents.append(name)

        for r in dirents: yield r

    def _wait_for_sync(self, target_id: str, action_type: str, timeout: int = 30):
        """Blocks until the specified action for the target is no longer pending/processing."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            # Check if action exists in queue
            row = self.db.fetchone("""
                SELECT status, last_error FROM actions 
                WHERE target_id = ? AND action_type = ? AND status IN ('pending', 'processing')
            """, (target_id, action_type))
            
            if not row:
                # Action gone means completed (or failed and removed, but we check object state separately)
                return True
            
            time.sleep(0.5)
        
        logger.warning(f"Timeout waiting for {action_type} on {target_id}")
        return False

    def open(self, path, flags):
        obj = self._resolve(path)
        if not obj or obj.type == 'folder': raise FuseOSError(errno.EISDIR)
        
        if isinstance(obj, DriveFile):
            # LAZY OPEN: Always return immediately.
            # ONLY trigger update check if file is ALREADY cached.
            # If not cached, we wait until 'read' to trigger download.
            if obj.present_locally:
                 self.db.enqueue_action(obj.id, 'ensure_latest', 'pull', priority=1)
            
            obj.open_count += 1
            obj.last_accessed = int(time.time())
            obj.update_cache_entry()
        
        self.fd += 1
        return self.fd

    def create(self, path, mode, fi=None):
        parent_path, name = os.path.split(path)
        parent_obj = self._resolve(parent_path)

        if not parent_obj or parent_obj.type != 'folder': raise FuseOSError(errno.ENOENT)
        if parent_obj.get_child(name): raise FuseOSError(errno.EEXIST)

        if parent_obj.id != 'drive_root' and not isinstance(parent_obj, DriveFolder):
             raise FuseOSError(errno.EACCES)

        # DriveFile.create_new_file splits the name internally
        new_obj = DriveFile.create_new_file(self.db, parent_obj.id, name)
        
        # Enqueue explicit UPLOAD action
        self.db.enqueue_action(new_obj.id, 'upload', 'push')
        
        if parent_path in self.path_to_id: del self.path_to_id[parent_path]
        self.fd += 1
        return self.fd

    def read(self, path, size, offset, fh):
        obj = self._resolve(path)
        if not obj or obj.type == 'folder': raise FuseOSError(errno.EISDIR)

        if isinstance(obj, DriveFile):
            if not obj.present_locally:
                # Check for Blacklisted Processes (File Managers/Indexers)
                uid, gid, pid = fuse.fuse_get_context()
                proc_name = self._get_process_name(pid) or "Unknown"

                if self._is_blacklisted_process(pid):
                    logger.info(f"Denying auto-download for {path} (PID: {pid}, Proc: {proc_name})")
                    raise FuseOSError(errno.EACCES) # Deny read access to prevent download

                # LAZY READ: Block here until download completes
                logger.info(f"Blocking read for {path} (Waiting for download) (PID: {pid}, Proc: {proc_name})")
                
                # Check if download is already in progress
                pending = self.db.fetchone(
                    "SELECT 1 FROM actions WHERE target_id=? AND action_type='ensure_latest' AND status IN ('pending', 'processing')", 
                    (obj.id,)
                )
                
                if not pending:
                    logger.info(f"Triggering on-demand download for {path} (PID: {pid}, Proc: {proc_name})")
                    self.db.enqueue_action(obj.id, 'ensure_latest', 'pull', priority=1)
                
                success = self._wait_for_sync(obj.id, 'ensure_latest', timeout=60)
                
                # Re-check status from DB
                fresh_row = self.db.fetchone("SELECT present_locally FROM drive_cache WHERE object_id=?", (obj.id,))
                if not fresh_row or not fresh_row['present_locally']:
                    logger.error(f"Read failed for {path}: File not present after sync wait.")
                    raise FuseOSError(errno.EIO)
                
                # Update local object state so read_local works
                obj.present_locally = 1

            try:
                return obj.read_local(size, offset)
            except FileNotFoundError:
                raise FuseOSError(errno.ENOENT)
        
        raise FuseOSError(errno.EIO)

    def write(self, path, data, offset, fh):
        obj = self._resolve(path)
        if not obj or obj.type == 'folder': raise FuseOSError(errno.EISDIR)
        
        if isinstance(obj, DriveFile):
            if not obj.present_locally: obj.create_local_placeholder()
            ret = obj.write_local(data, offset)
            # Enqueue UPDATE_CONTENT action
            self.db.enqueue_action(obj.id, 'update_content', 'push')
            return ret
        
        raise FuseOSError(errno.EIO)

    def truncate(self, path, length, fh=None):
        obj = self._resolve(path)
        if not obj or obj.type == 'folder': raise FuseOSError(errno.EISDIR)

        if isinstance(obj, DriveFile):
            local_path = obj.get_local_full_path()
            if not os.path.exists(local_path): 
                obj.create_local_placeholder()
                local_path = obj.get_local_full_path()
            
            with open(local_path, 'r+b') as f:
                f.truncate(length)
            
            obj.size = length
            obj.dirty = 1
            obj.local_modified_at = int(time.time())
            obj.commit()
            
            self.db.enqueue_action(obj.id, 'update_content', 'push')
        else:
            raise FuseOSError(errno.EIO)

    def unlink(self, path):
        parent_path, name = os.path.split(path)
        parent_obj = self._resolve(parent_path)
        if not parent_obj: raise FuseOSError(errno.ENOENT)

        obj = parent_obj.get_child(name)
        if not obj: raise FuseOSError(errno.ENOENT)
        if obj.type == 'folder': raise FuseOSError(errno.EISDIR)
        
        self._soft_delete(obj)
        self._invalidate_path_cache(path, obj.id, parent_path)

    def rmdir(self, path):
        parent_path, name = os.path.split(path)
        parent_obj = self._resolve(parent_path)
        if not parent_obj: raise FuseOSError(errno.ENOENT)

        obj = parent_obj.get_child(name)
        if not obj: raise FuseOSError(errno.ENOENT)
        if obj.type != 'folder': raise FuseOSError(errno.ENOTDIR)
        if obj.list_children(): raise FuseOSError(errno.ENOTEMPTY)

        self._soft_delete(obj)
        self._invalidate_path_cache(path, obj.id, parent_path)

    def mkdir(self, path, mode):
        parent_path, name = os.path.split(path)
        parent_obj = self._resolve(parent_path)
        if not parent_obj: raise FuseOSError(errno.ENOENT)
        if parent_obj.get_child(name): raise FuseOSError(errno.EEXIST)

        new_obj = DriveFolder.create_new_folder(self.db, parent_obj.id, name)
        # Use upload/create for folder
        self.db.enqueue_action(new_obj.id, 'upload', 'push')
        
        if parent_path in self.path_to_id: del self.path_to_id[parent_path]

    def rename(self, old_path, new_path):
        old_parent, old_name = os.path.split(old_path)
        new_parent, new_name = os.path.split(new_path)

        obj = self._resolve(old_path)
        if not obj: raise FuseOSError(errno.ENOENT)

        dest_parent_obj = self._resolve(new_parent)
        if not dest_parent_obj: raise FuseOSError(errno.ENOENT)
        
        # Check overwrite
        target_obj = dest_parent_obj.get_child(new_name)
        if target_obj:
            if target_obj.type == 'folder': 
                raise FuseOSError(errno.EEXIST)
            logger.info(f"Rename overwriting existing file: {new_name}")
            self._soft_delete(target_obj)
            self._invalidate_path_cache(new_path, target_obj.id, new_parent)

        # Detect intent
        is_rename = (old_name != new_name)
        is_move = (obj.parent_id != dest_parent_obj.id)
        original_parent_id = obj.parent_id

        # Update Object Locally
        if obj.type == 'file' and '.' in new_name:
            base, ext = new_name.rsplit('.', 1)
            obj.name = base
            obj.extension = ext
        else:
            obj.name = new_name
            obj.extension = None
            
        obj.parent_id = dest_parent_obj.id
        obj.dirty = 1
        obj.local_modified_at = int(time.time())
        # Manual update
        self.db.execute("UPDATE objects SET name=?, extension=?, parent_id=?, local_modified_at=?, dirty=1 WHERE id=?", 
                        (obj.name, obj.extension, obj.parent_id, obj.local_modified_at, obj.id))

        # Enqueue specific actions
        if is_move:
            # For move actions, capture the object's parent_id at the time of enqueue
            metadata = {'original_parent_id': original_parent_id}
            self.db.enqueue_action(obj.id, 'move', 'push', destination=dest_parent_obj.id, metadata=metadata)
        if is_rename:
            # For rename, destination is the new full name
            full_new_name = new_name # Assuming FUSE passed new_name fully
            self.db.enqueue_action(obj.id, 'rename', 'push', destination=full_new_name)

        self._invalidate_path_cache(old_path, obj.id, old_parent)
        self._invalidate_path_cache(new_path, None, new_parent)

    def _soft_delete(self, obj):
        # Soft delete in DB
        self.db.execute("UPDATE objects SET deleted=1, dirty=1, sync_state=?, local_modified_at=? WHERE id=?", 
                        (SYNC_STATE_PENDING_PUSH, int(time.time()), obj.id))
        self.db.enqueue_action(obj.id, 'delete', 'push')
        
        # Clean local cache if file
        if isinstance(obj, DriveFile):
            p = obj.get_local_full_path()
            if os.path.exists(p): os.remove(p)

    def _invalidate_path_cache(self, path, obj_id, parent_path):
        if path in self.path_to_id: del self.path_to_id[path]
        if obj_id and obj_id in self.id_to_path: del self.id_to_path[obj_id]
        if parent_path in self.path_to_id: del self.path_to_id[parent_path]

def mount_daemon(db_path, mount_point):
    if not os.path.exists(mount_point): os.makedirs(mount_point)
    FUSE(OrchardFS(db_path), mount_point, foreground=True)