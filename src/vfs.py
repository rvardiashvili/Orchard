import os
import errno
import logging
import stat
import time
import json
import threading
from fuse import FUSE, FuseOSError, Operations

logger = logging.getLogger(__name__)

NODE_NOT_FOUND = object() # Sentinel value for negative caching

class RealCloudFS(Operations):
    """
    A FUSE filesystem that connects to Real iCloud Drive.
    """

    def __init__(self, api, root_cache_dir, local_mappings=None):
        self.api = api
        self.root_cache = root_cache_dir
        self.local_mappings = local_mappings or {} 
        
        # Cache for directory listings to avoid API spam
        # Structure: { '/path/to/dir': {timestamp: 123, children: ['Name1', 'Name2']} }
        self.dir_cache = {}
        self.CACHE_TTL = 3600 # 1 Hour
        
        # Persistence file
        self.structure_file = os.path.join(self.root_cache, "dir_structure.json")
        self._load_cache()
        
        # Node cache to store file attributes (size, etc)
        # Structure: { '/path/to/file': node_object }
        self.node_cache = {}
        self.NODE_CACHE_TTL = 300 # 5 minutes for node objects (positive cache)

        # Negative cache for non-existent nodes
        # Structure: { '/path/to/file': timestamp_of_non_existence }
        self.negative_node_cache = {}
        self.NEGATIVE_CACHE_TTL = 30 # 30 seconds for non-existent nodes

        # Cache for getattr results (attribute dictionaries)
        # Structure: { '/path/to/file': {'timestamp': 123, 'attrs': {...}} }
        self.getattr_cache = {}
        self.GETATTR_CACHE_TTL = 5 # 5 seconds for getattr results (very short TTL)

        # Pending uploads mapping: virtual path -> local cache path
        self.pending_uploads = {}
        
        # Cache Eviction Tracking
        self.last_access = {}
        self.CACHE_RETENTION_TIME = 60 # 1 minute grace period

        # Start background uploader/cleaner thread
        try:
            t = threading.Thread(target=self._uploader_thread, daemon=True)
            t.start()
        except Exception:
            logger.debug("Failed to start uploader thread")
        
        # Lock for directory refresh to prevent stampeding
        self.refresh_lock = threading.Lock()

    def _get_local_mapped_path(self, path):
        """
        Returns the absolute local path if the virtual path is mapped.
        Example: /Downloads/foo.pdf -> /home/rati/Downloads/foo.pdf
        """
        parts = [p for p in path.split('/') if p]
        if not parts: return None
        
        # Check root level mapping
        if parts[0] in self.local_mappings:
            local_root = self.local_mappings[parts[0]]
            # Append the rest
            return os.path.join(local_root, *parts[1:])
        return None

    def _load_cache(self):
        try:
            if os.path.exists(self.structure_file):
                with open(self.structure_file, 'r') as f:
                    self.dir_cache = json.load(f)
                logger.info(f"Loaded {len(self.dir_cache)} directory listings from disk cache: {self.structure_file}")
                logger.debug(f"Sample loaded cache keys: {list(self.dir_cache.keys())[:5]}")
            else:
                logger.info("No directory cache found. Starting fresh.")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to load structure cache due to JSON error: {e}. Cache file might be corrupted.")
            if os.path.exists(self.structure_file): # Attempt to remove corrupted file
                os.remove(self.structure_file)
                logger.info("Removed corrupted dir_structure.json.")
        except Exception as e:
            logger.warning(f"Failed to load structure cache: {e}")

    def _save_cache(self):
        try:
            # Atomic write (write to temp then rename) desirable but simple write for now
            with open(self.structure_file, 'w') as f:
                json.dump(self.dir_cache, f)
            logger.debug(f"Saved {len(self.dir_cache)} directory listings to disk cache: {self.structure_file}")
            logger.debug(f"Sample saved cache keys: {list(self.dir_cache.keys())[:5]}")
        except Exception as e:
            logger.warning(f"Failed to save structure cache: {e}")

    def _get_node(self, path):
        """
        Traverses the iCloud Drive tree to find the node at 'path'.
        Returns the pyicloud node object or NODE_NOT_FOUND if not found.
        """
        logger.debug(f"[_get_node] Requested path: {path}")
        
        # 1. Check negative node cache
        now = time.time()
        if path in self.negative_node_cache:
            if now - self.negative_node_cache[path] < self.NEGATIVE_CACHE_TTL:
                logger.debug(f"[_get_node] Negative cache HIT for {path}")
                return NODE_NOT_FOUND
            else:
                logger.debug(f"[_get_node] Negative cache EXPIRED for {path}")
                del self.negative_node_cache[path] # Clean up expired entry

        # 2. Check positive node cache
        if path in self.node_cache:
            entry = self.node_cache[path]
            if now - entry['timestamp'] < self.NODE_CACHE_TTL:
                logger.debug(f"[_get_node] Node cache HIT for {path} (active)")
                return entry['node']
            else:
                logger.debug(f"[_get_node] Node cache EXPIRED for {path}")
                del self.node_cache[path] # Clean up expired entry

        logger.debug(f"[_get_node] Node cache MISS for {path}")

        # Fetch from iCloud
        node = None
        try:
            if path == '/':
                node = self.api.drive
            else: # Standard Drive path
                parts = [p for p in path.split('/') if p]
                current_node = self.api.drive
                for part in parts:
                    current_node = current_node[part]
                node = current_node
        except KeyError:
            logger.debug(f"[_get_node] KeyError for {path}")
            pass # Node not found, handled below
        except Exception as e:
            logger.error(f"[_get_node] Error fetching {path}: {e}")
            pass # Node not found, handled below

        if node: # If a node was successfully found
            self.node_cache[path] = {'timestamp': now, 'node': node}
            return node
        else: # Node not found after fetching attempt
            self.negative_node_cache[path] = now # Cache non-existence
            return NODE_NOT_FOUND

    def getattr(self, path, fh=None):
        """
        Get file attributes.
        """
        # Filter out common OS noise to avoid API spam
        # Photos is removed, so we explicitly ignore it here to stop the OS from probing it repeatedly
        ignored_paths = {'/Photos', '/.Trash', '/.hidden', '/.Trash-1000'}
        if path in ignored_paths or path.startswith('/Photos/'):
            raise FuseOSError(errno.ENOENT)

        logger.debug(f"[getattr] Requested path: {path}")
        now = time.time()

        # 1. Check getattr cache
        if path in self.getattr_cache:
            entry = self.getattr_cache[path]
            if now - entry['timestamp'] < self.GETATTR_CACHE_TTL:
                logger.debug(f"[getattr] Getattr cache HIT for {path}")
                return entry['attrs']
            else:
                logger.debug(f"[getattr] Getattr cache EXPIRED for {path}")
                del self.getattr_cache[path] # Clean up expired entry
        logger.debug(f"[getattr] Getattr cache MISS for {path}")

        # Root is always a dir
        if path == '/':
            logger.debug("[getattr] Path is root '/'")
            attrs = dict(st_mode=(stat.S_IFDIR | 0o777), st_nlink=2, st_size=0, st_ctime=now, st_mtime=now, st_atime=now)
            self.getattr_cache[path] = {'timestamp': now, 'attrs': attrs}
            return attrs
            
        # 2. Check Local Mappings
        local_path = self._get_local_mapped_path(path)
        if local_path and os.path.exists(local_path):
            logger.debug(f"[getattr] Path '{path}' hit local mapping: {local_path}")
            # Serve local file attributes
            st = os.lstat(local_path)
            attrs = dict(st_mode=st.st_mode, st_nlink=st.st_nlink, st_size=st.st_size,
                         st_ctime=st.st_ctime, st_mtime=st.st_mtime, st_atime=st.st_atime)
            self.getattr_cache[path] = {'timestamp': now, 'attrs': attrs}
            return attrs

        # 2b. Serve files that exist in the local cache (writes/uploads)
        local_cache_path = os.path.join(self.root_cache, path.lstrip('/'))
        if os.path.exists(local_cache_path):
            logger.debug(f"[getattr] Path '{path}' hit local cache: {local_cache_path}")
            st = os.lstat(local_cache_path)
            attrs = dict(st_mode=st.st_mode, st_nlink=st.st_nlink, st_size=st.st_size,
                         st_ctime=st.st_ctime, st_mtime=st.st_mtime, st_atime=st.st_atime)
            self.getattr_cache[path] = {'timestamp': now, 'attrs': attrs}
            return attrs

        node = self._get_node(path)
        if node is NODE_NOT_FOUND:
            logger.debug(f"[getattr] Node not found for path: {path} (from negative cache or _get_node). Attempting direct API lookup to recover.")
            # Attempt a direct traversal on the API to recover from stale negative cache
            try:
                parts = [p for p in path.split('/') if p]
                if not parts:
                    node = self.api.drive
                else:
                    cur = self.api.drive
                    for part in parts:
                        cur = cur[part]
                    node = cur
                if node and node is not NODE_NOT_FOUND:
                    # Clear negative cache for this path and cache the node
                    try:
                        if path in self.negative_node_cache:
                            del self.negative_node_cache[path]
                    except Exception:
                        pass
                    self.node_cache[path] = {'timestamp': time.time(), 'node': node}
                    logger.debug(f"[getattr] Recovered node for {path} via direct API traversal")
                else:
                    raise FuseOSError(errno.ENOENT)
            except FuseOSError:
                # If remote check explicitly failed (ENOENT), check local cache one last time
                # This covers the case where readdir saw a local file, but _get_node failed remotely
                # and the local file check at the top of getattr missed it (maybe race condition or logic gap)
                local_cache_path = os.path.join(self.root_cache, path.lstrip('/'))
                if os.path.exists(local_cache_path):
                     logger.debug(f"[getattr] Node not found remotely but exists locally. Serving {local_cache_path}")
                     st = os.lstat(local_cache_path)
                     attrs = dict(st_mode=st.st_mode, st_nlink=st.st_nlink, st_size=st.st_size,
                                 st_ctime=st.st_ctime, st_mtime=st.st_mtime, st_atime=st.st_atime)
                     self.getattr_cache[path] = {'timestamp': now, 'attrs': attrs}
                     return attrs
                raise
            except Exception as e:
                logger.debug(f"[getattr] Direct API lookup failed for {path}: {e}")
                raise FuseOSError(errno.ENOENT)
        elif not node:
            logger.debug(f"[getattr] No node found for path: {path} (unexpected _get_node result)")
            # This case should ideally not happen if NODE_NOT_FOUND is used consistently
            raise FuseOSError(errno.ENOENT)

        try:
            is_dir = False
            size = 0
            
            logger.debug(f"[getattr] Processing Drive node for {path}")
            # If it has a 'dir' method or type is folder
            if hasattr(node, 'type'):
                if node.type == 'folder':
                    is_dir = True
            elif hasattr(node, 'dir'): # Root or folder-like
                is_dir = True
            size = node.size if hasattr(node, 'size') and node.size else 0
                
            mode = (stat.S_IFDIR | 0o777) if is_dir else (stat.S_IFREG | 0o666)
            
            # Times
            now = time.time()
            attrs = dict(st_mode=mode, st_nlink=2, st_size=size, st_ctime=now, st_mtime=now, st_atime=now)
            self.getattr_cache[path] = {'timestamp': now, 'attrs': attrs} # Cache the computed attributes
            return attrs
            
        except Exception as e:
            logger.error(f"getattr error for {path}: {e}")
            raise FuseOSError(errno.EIO)

    def _refresh_dir_background(self, path, node, blocking=False):
        """
        Fetches directory contents in background and updates cache.
        """
        # Allow forcing a blocking refresh when called synchronously
        if blocking:
            logger.debug(f"[_refresh_dir_background] Blocking refresh requested for {path}")
            self.refresh_lock.acquire()
        else:
            # Prevent concurrent refreshes for the same path
            if not self.refresh_lock.acquire(blocking=False):
                logger.debug(f"[_refresh_dir_background] Skip {path} - locked.")
                return

        try:
            logger.debug(f"[_refresh_dir_background] Triggered for path: {path}")
            # Normalize
            if len(path) > 1 and path.endswith('/'):
                path = path.rstrip('/')

            entries = set()
            # Drive Listing (Cloud)
            try:
                children = node.dir()
                now = time.time()
                for child_name in children:
                    if child_name:
                        entries.add(str(child_name))
                        
                        # Optimization: Cache the node immediately
                        # This prevents _get_node from having to re-traverse the tree for every file
                        try:
                            child_node = node[child_name]
                            full_path = os.path.join(path, child_name)
                            self.node_cache[full_path] = {'timestamp': now, 'node': child_node}
                        except Exception as e:
                            logger.warning(f"Failed to pre-cache node {child_name}: {e}")
            except Exception as e:
                logger.error(f"Error listing drive {path}: {e}")
            
            # Update Cache
            self.dir_cache[path] = {
                'timestamp': time.time(),
                'children': list(entries)
            }
            logger.debug(f"Saving background refresh for {path}: {list(entries)[:5]}...")
            self._save_cache()

            # --- SYNC: Remove local files that are deleted remotely ---
            # We only do this if the remote listing was successful (entries is not empty or we trust it)
            # Actually, if the folder is empty remotely, entries will be empty.
            
            try:
                local_cache_path = os.path.join(self.root_cache, path.lstrip('/'))
                if os.path.isdir(local_cache_path):
                    local_files = os.listdir(local_cache_path)
                    
                    # Protected system files that exist locally but not remotely
                    protected_files = {
                        'dir_structure.json', 
                        'reminders.json', 
                        'contacts.vcf', 
                        '.clipboard',
                        'LinuxSync',
                        'Notes',
                        'cloud_map.json'
                    }

                    for f in local_files:
                        # 1. Skip protected internal files
                        if f in protected_files or f.startswith('.'):
                            continue
                            
                        full_v_path = os.path.join(path, f)
                        # Normalize path for pending_uploads check (it uses full virtual paths)
                        # If pending upload, DO NOT delete
                        if full_v_path in self.pending_uploads:
                            continue
                        
                        # If file is not in remote entries, delete it locally
                        if f not in entries:
                            l_path = os.path.join(local_cache_path, f)
                            logger.info(f"Sync: Removing local file {f} because it is missing remotely.")
                            try:
                                if os.path.isdir(l_path):
                                    import shutil
                                    shutil.rmtree(l_path)
                                else:
                                    os.remove(l_path)
                                # Also remove from negative cache if it was there (to be safe)
                                if full_v_path in self.negative_node_cache:
                                    del self.negative_node_cache[full_v_path]
                            except Exception as ex:
                                logger.warning(f"Failed to remove stale local file {f}: {ex}")

            except Exception as e:
                logger.error(f"Error during local sync prune: {e}")

            # logger.info(f"Background refresh complete for {path}")
        except Exception as e:
            logger.error(f"Background refresh failed for {path}: {e}")
        finally:
            self.refresh_lock.release()

    def readdir(self, path, fh):
        """
        List directory contents.
        """
        logger.debug(f"[readdir] Requested path: {path}")
        # Normalize path (remove trailing slash unless root)
        clean_path = path
        if len(path) > 1 and path.endswith('/'):
            clean_path = path.rstrip('/')
            
        
        # Always include . and ..
        yield ('.', dict(st_mode=(stat.S_IFDIR | 0o777), st_nlink=2), 0)
        yield ('..', dict(st_mode=(stat.S_IFDIR | 0o777), st_nlink=2), 0)

        entries = set()
        
        # Debugging for empty root folder
        if clean_path == '/':
            logger.debug(f"readdir called for root ('/'), clean_path: {clean_path}")
            logger.debug(f"Initial entries for root: {entries}")

        # 1. Add Local Mapped Files
        parts = [p for p in path.split('/') if p]
        if parts and parts[0] in self.local_mappings:
            local_path = self._get_local_mapped_path(path)
            if local_path and os.path.isdir(local_path):
                try:
                    local_entries = os.listdir(local_path)
                    entries.update(local_entries)
                except OSError: pass

        # 1b. Add Local Cache Files (Pending uploads / optimistic creations)
        local_cache_path = os.path.join(self.root_cache, clean_path.lstrip('/'))
        if os.path.isdir(local_cache_path):
            try:
                local_entries = os.listdir(local_cache_path)
                entries.update(local_entries)
                logger.debug(f"Added local cache entries for {clean_path}: {local_entries}")
            except OSError: pass

        # Check Cache for Cloud Entries
        if clean_path in self.dir_cache:
            logger.debug(f"Cache HIT for {clean_path}")
            entry = self.dir_cache[clean_path]
            
            # Serve Cached content, filter out . and ..
            cached_children = [c for c in entry['children'] if c and c not in ('.', '..')]
            entries.update(cached_children)
            
            # If Stale, trigger background refresh
            # But DO NOT wait.
            if time.time() - entry['timestamp'] > self.CACHE_TTL:
                # We need the node to refresh - spawn background thread
                node = self._get_node(clean_path)
                if node:
                    threading.Thread(target=self._refresh_dir_background, args=(clean_path, node), daemon=True).start()
            
            logger.debug(f"Final entries (from cache) for {clean_path} before yielding: {entries}")
            logger.debug(f"[readdir] Yielding {len(entries)} entries for {clean_path}")
            for name in entries:
                if not name: continue # Skip empty names
                
                full_path = os.path.join(path, name)
                attrs = None
                try:
                    # Need to get attributes for the item
                    attrs = self.getattr(full_path)
                except FuseOSError as e:
                    logger.debug(f"getattr failed for {full_path} in readdir: {e}")
                    # If getattr fails, we cannot yield that item. Skip it.
                    continue
                except Exception as e:
                    logger.error(f"Unexpected error getting attrs for {full_path}: {e}")
                    continue

                yield (name, attrs, 0)
            return
        
        logger.debug(f"Cache MISS for {clean_path} - Fetching synchronously")

        # Fetch from Cloud (First time access - must block)
        node = self._get_node(clean_path)

        if node:
            try:
                # reuse background logic but run synchronously for first time (force blocking)
                self._refresh_dir_background(clean_path, node, blocking=True)
                # Re-read from cache
                if clean_path in self.dir_cache:
                    # Filter out . and ..
                    cached_children = [c for c in self.dir_cache[clean_path]['children'] if c and c not in ('.', '..')]
                    entries.update(cached_children)
            except Exception as e:
                logger.error(f"readdir error {clean_path}: {e}")
        
        logger.debug(f"Final entries (from cloud) for {clean_path} before yielding: {entries}")
        for name in entries:
            if not name: continue
            
            full_path = os.path.join(path, name)
            attrs = None
            try:
                attrs = self.getattr(full_path)
            except FuseOSError as e:
                logger.debug(f"getattr failed for {full_path} in readdir: {e}")
                continue
            except Exception as e:
                logger.error(f"Unexpected error getting attrs for {full_path}: {e}")
                continue
            yield (name, attrs, 0)

    def opendir(self, path, fh=None):
        """
        Called when a directory is opened. Force a blocking refresh for /Photos
        to ensure album listings are populated even if getattr results are cached.
        """
        logger.debug(f"[opendir] Opened dir: {path}")

        # If it's the Photos tree, ensure cache is populated synchronously
        try:
            clean_path = path
            if len(path) > 1 and path.endswith('/'):
                clean_path = path.rstrip('/')

            # Generic directory: try to refresh so remote-created folders appear locally
            try:
                node = self._get_node(clean_path)
                if node and node is not NODE_NOT_FOUND:
                    # If node looks like a directory, refresh synchronously
                    if hasattr(node, 'dir') or getattr(node, 'type', '') == 'folder' or hasattr(node, '__iter__'):
                        logger.debug(f"[opendir] Forcing refresh for {clean_path}")
                        self._refresh_dir_background(clean_path, node, blocking=True)
            except Exception as e:
                logger.debug(f"[opendir] Generic refresh failed for {clean_path}: {e}")
        except Exception as e:
            logger.error(f"opendir error for {path}: {e}")

        return 0

    def read(self, path, length, offset, fh):
        """
        Read data from a file.
        """
        # 1. Read from Local Mapping (Direct Passthrough)
        local_mapped = self._get_local_mapped_path(path)
        if local_mapped and os.path.exists(local_mapped):
            with open(local_mapped, 'rb') as f:
                f.seek(offset)
                return f.read(length)

        # 2. Cloud Read
        node = self._get_node(path)
        
        # Local cache path
        local_path = os.path.join(self.root_cache, path.lstrip('/'))

        # Check for sentinel or missing node
        if node is NODE_NOT_FOUND or not node:
             # If we have a local file, serve it (optimistic read for new/pending files)
             if os.path.exists(local_path):
                 logger.debug(f"read: Node not found remotely but exists locally. Serving {local_path}")
                 with open(local_path, 'rb') as f:
                     f.seek(offset)
                     return f.read(length)
             raise FuseOSError(errno.ENOENT)
        
        # Determine size logic
        remote_size = node.size
        
        # Check if local file exists and is valid size-wise
        if not os.path.exists(local_path) or os.path.getsize(local_path) != remote_size:
            logger.info(f"Downloading {path} ({remote_size} bytes)...")
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            
            try:
                # Drive Download
                with node.open(stream=True) as response:
                    with open(local_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
            except Exception as e:
                logger.error(f"Download failed for {path}: {e}")
                raise FuseOSError(errno.EIO)

        # Read from local file
        with open(local_path, 'rb') as f:
            f.seek(offset)
            data = f.read(length)
            
        # Update keep-alive
        self.last_access[path] = time.time()
        return data

    def write(self, path, buf, offset, fh):
        # 1. Write to Local Mapping (Direct)
        local_mapped = self._get_local_mapped_path(path)
        if local_mapped:
            # Simple write passthrough
            with open(local_mapped, 'r+b' if os.path.exists(local_mapped) else 'wb') as f:
                f.seek(offset)
                f.write(buf)
            return len(buf)

        # 2. Cloud write: write to local cache and mark for upload on release
        local_path = os.path.join(self.root_cache, path.lstrip('/'))
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        # open in r+b or create
        mode = 'r+b' if os.path.exists(local_path) else 'wb'
        with open(local_path, mode) as f:
            f.seek(offset)
            f.write(buf)

        # Mark pending upload
        self.pending_uploads[path] = local_path
        try:
            size = os.path.getsize(local_path)
        except Exception:
            size = None
        logger.debug(f"Marked {path} for upload (local cache: {local_path}) size={size}")
        return len(buf)

    def download_all(self):
        """
        Recursively downloads ALL files from iCloud Drive to local cache.
        WARNING: This can take a long time and use a lot of disk space.
        """
        logger.info("Starting Full Download of iCloud Drive...")
        
        def _recursive_download(node, relative_path=""):
            # Iterate children
            try:
                children = node.dir()
                for name in children:
                    child_node = node[name]
                    child_path = os.path.join(relative_path, name)
                    
                    # Check type (folder or file)
                    # node.type isn't always reliable, check dir() capability
                    # But simpler: try-except
                    
                    try:
                        # Try to descend (Treat as folder)
                        # Just checking .dir() is not enough, need to access it
                        # If it has .dir(), it's a folder
                        if hasattr(child_node, 'dir'):
                             _recursive_download(child_node, child_path)
                        else:
                            # It's a file
                            self._ensure_downloaded(child_node, child_path)
                    except Exception:
                        # If it fails to treat as folder, it might be a file (or vice versa)
                        # Actually child_node IS the node. 
                        # PyiCloud nodes usually have .type
                        if getattr(child_node, 'type', '') == 'file':
                             self._ensure_downloaded(child_node, child_path)
            except Exception as e:
                logger.error(f"Error traversing {relative_path}: {e}")

        _recursive_download(self.api.drive)
        logger.info("Full Download Complete.")

    def _ensure_downloaded(self, node, path):
        local_path = os.path.join(self.root_cache, path.lstrip('/'))
        
        if not os.path.exists(local_path) or os.path.getsize(local_path) != node.size:
            logger.info(f"Downloading {path} ({node.size} bytes)...")
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            try:
                with node.open(stream=True) as response:
                    with open(local_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
            except Exception as e:
                logger.error(f"Download failed for {path}: {e}")

    def _upload_file(self, path, local_path):
        """
        Attempt to upload `local_path` to the cloud at virtual `path`.
        Uses a best-effort set of API method names to support different backends.
        """
        logger.info(f"Uploading {path} from {local_path}...")
        parts = [p for p in path.split('/') if p]
        parent = '/' if len(parts) <= 1 else '/' + '/'.join(parts[:-1])
        name = parts[-1] if parts else ''

        try:
            parent_node = self._get_node(parent)
            if parent_node is NODE_NOT_FOUND:
                logger.error(f"Upload failed: parent {parent} not found")
                # Try to refresh parent directory cache and re-fetch
                try:
                    pn = None
                    # attempt to get parent node directly from API if possible
                    parts = [p for p in parent.split('/') if p]
                    if not parts:
                        pn = self.api.drive
                    else:
                        cur = self.api.drive
                        for part in parts:
                            cur = cur[part]
                        pn = cur
                    if pn:
                        parent_node = pn
                except Exception as e:
                    logger.debug(f"Parent refresh attempt failed: {e}")
            if parent_node is NODE_NOT_FOUND:
                return False

            # If target exists, try to update it
            existing = self._get_node(path)
            if existing and existing is not NODE_NOT_FOUND:
                # Try common update methods
                try:
                    if hasattr(existing, 'replace'):
                        with open(local_path, 'rb') as f:
                            existing.replace(f)
                        logger.debug("Used existing.replace() to update file")
                        return True
                    if hasattr(existing, 'update'):
                        with open(local_path, 'rb') as f:
                            existing.update(f)
                        logger.debug("Used existing.update() to update file")
                        return True
                except Exception as e:
                    logger.debug(f"Existing-node update attempts failed: {e}")

            # Otherwise attempt upload/create on parent
            # Try several possible method names
            candidates = [
                ('upload', True),      # parent.upload(fileobj, name)
                ('create', True),      # parent.create(name, fileobj)
                ('add', True),         # parent.add(fileobj)
                ('put', True),         # parent.put(name, fileobj)
                ('__call__', False),   # parent(name=...)
            ]

            for method, takes_name in candidates:
                if not hasattr(parent_node, method):
                    continue
                m = getattr(parent_node, method)
                with open(local_path, 'rb') as f:
                    tried = []
                    # Try a sequence of likely call signatures until one works
                    if takes_name:
                        call_options = [lambda: m(f, name), lambda: m(name, f), lambda: m(f), lambda: m(name)]
                    else:
                        call_options = [lambda: m(name), lambda: m(f), lambda: m()]

                    for call in call_options:
                        try:
                            call()
                            logger.info(f"Uploaded using parent.{method} for {path}")
                            return True
                        except TypeError as te:
                            tried.append(str(te))
                            continue
                        except Exception as e:
                            # If an unexpected error occurs, log and try next method
                            logger.debug(f"parent.{method} attempt raised: {e}")
                            tried.append(str(e))
                            break
                    if tried:
                        logger.debug(f"parent.{method} tried signatures resulted in: {tried}")

            # As a last resort, try setting via indexing if supported
            try:
                if hasattr(parent_node, '__setitem__'):
                    with open(local_path, 'rb') as f:
                        parent_node[name] = f.read()
                    logger.info("Uploaded using parent_node[name] assignment")
                    return True
            except Exception as e:
                logger.debug(f"Index assignment upload failed: {e}")

            logger.error(f"No supported upload method found for parent {parent}")
            return False
        except Exception as e:
            logger.error(f"_upload_file exception for {path}: {e}")
            return False

    def _maybe_upload(self, path):
        """If path is pending upload, attempt to upload and clear pending state."""
        if path not in self.pending_uploads:
            return
        local_path = self.pending_uploads[path]
        logger.debug(f"_maybe_upload attempting upload for {path} (local={local_path})")
        success = self._upload_file(path, local_path)
        if success:
            try:
                del self.pending_uploads[path]
            except KeyError:
                pass
        else:
            logger.error(f"Upload failed for {path}; will retry on next release")

    def _uploader_thread(self):
        """Background thread to retry pending uploads and evict stale cache."""
        logger.debug("uploader/cleaner thread started")
        while True:
            # 1. Retry Pending Uploads
            try:
                pending = list(self.pending_uploads.keys())
                for path in pending:
                    try:
                        self._maybe_upload(path)
                    except Exception as e:
                        logger.debug(f"uploader thread _maybe_upload error for {path}: {e}")
            except Exception as e:
                logger.debug(f"uploader thread error: {e}")

            # 2. Evict Stale Cache Files
            try:
                now = time.time()
                # Create list to avoid modification during iteration
                to_check = list(self.last_access.keys())
                for path in to_check:
                    last_seen = self.last_access[path]
                    # If file is old enough AND not currently pending upload
                    if (now - last_seen > self.CACHE_RETENTION_TIME) and (path not in self.pending_uploads):
                        local_path = os.path.join(self.root_cache, path.lstrip('/'))
                        if os.path.exists(local_path):
                            try:
                                # Double check if file is open? 
                                # We can't easily check lsof, but if it's open, os.remove works on Linux (unlinks) 
                                # but keeps fd open for process. This is actually fine.
                                os.remove(local_path)
                                logger.debug(f"[Cache Cleaner] Evicted {path} (Idle for {int(now-last_seen)}s)")
                                # Clear getattr cache
                                if path in self.getattr_cache:
                                    del self.getattr_cache[path]
                                del self.last_access[path]
                            except Exception as e:
                                logger.warning(f"Failed to evict {path}: {e}")
                        else:
                            # File gone already
                            del self.last_access[path]
            except Exception as e:
                logger.debug(f"Cache cleaner error: {e}")

            time.sleep(5)

    def destroy(self, path):
        """
        Called on filesystem unmount. Save cache.
        """
        logger.info("Unmounting and saving cache...")
        self._save_cache()

    def open(self, path, flags):
        """Open is required by some FUSE wrappers; just ensure cache file exists."""
        local_path = os.path.join(self.root_cache, path.lstrip('/'))
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        if not os.path.exists(local_path):
            # create an empty file so reads/writes can proceed
            open(local_path, 'a').close()
        # Return a real file descriptor to FUSE
        try:
            fd = os.open(local_path, flags)
            return fd
        except Exception:
            return 0

    def create(self, path, mode, fi=None):
        """Create a file in the local cache and mark for upload."""
        local_path = os.path.join(self.root_cache, path.lstrip('/'))
        parent = '/' if '/' not in path.rstrip('/') else '/' + '/'.join([p for p in path.split('/') if p][:-1])
        name = [p for p in path.split('/') if p][-1]
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        open(local_path, 'wb').close()
        self.pending_uploads[path] = local_path
        logger.debug(f"create: local_path={local_path} parent={parent} name={name}")
        # Update dir_cache for parent so readdir sees the new file immediately
        try:
            if parent in self.dir_cache:
                children = set(self.dir_cache[parent].get('children', []))
                children.add(name)
                self.dir_cache[parent]['children'] = list(children)
                self.dir_cache[parent]['timestamp'] = time.time()
            else:
                self.dir_cache[parent] = {'timestamp': time.time(), 'children': [name]}
        except Exception as e:
            logger.debug(f"create: failed to update dir_cache for {parent}: {e}")

        # Clear negative cache for the new file
        if path in self.negative_node_cache:
            del self.negative_node_cache[path]

        logger.debug(f"Created local file for {path} and marked for upload (local={local_path})")
        try:
            fd = os.open(local_path, os.O_RDWR)
            return fd
        except Exception:
            return 0

    def release(self, path, fh):
        """Called when file handle is closed; attempt upload if pending. Mark for eviction."""
        # Close fd if provided
        try:
            if isinstance(fh, int) and fh > 0:
                try:
                    os.close(fh)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self._maybe_upload(path)
        except Exception as e:
            logger.error(f"release error for {path}: {e}")
        
        # Mark for graceful eviction
        self.last_access[path] = time.time()
            
        return 0

    def mknod(self, path, mode, dev):
        """Support mknod by forwarding to create."""
        logger.debug(f"mknod called for {path} mode={mode} dev={dev}")
        return self.create(path, mode)

    def flush(self, path, fh):
        """Flush is called to flush dirty data; attempt upload."""
        logger.debug(f"flush called for {path} fh={fh}")
        try:
            self._maybe_upload(path)
        except Exception as e:
            logger.error(f"flush upload error for {path}: {e}")
        return 0

    def fsync(self, path, datasync, fh):
        """fsync called by some programs to ensure data is written."""
        logger.debug(f"fsync called for {path} datasync={datasync} fh={fh}")
        try:
            self._maybe_upload(path)
        except Exception as e:
            logger.error(f"fsync upload error for {path}: {e}")
        return 0

    def unlink(self, path):
        """Attempt to delete remote file; also remove local cache."""
        logger.debug(f"unlink requested for {path}")
        try:
            node = self._get_node(path)
            if node and node is not NODE_NOT_FOUND and hasattr(node, 'delete'):
                try:
                    node.delete()
                    logger.info(f"Deleted remote {path}")
                except Exception as e:
                    logger.error(f"Remote delete failed for {path}: {e}")
            # remove local cache if present
            local_path = os.path.join(self.root_cache, path.lstrip('/'))
            if os.path.exists(local_path):
                os.remove(local_path)
            # clear pending upload if any
            if path in self.pending_uploads:
                del self.pending_uploads[path]
        except Exception as e:
            logger.error(f"unlink error for {path}: {e}")
            raise FuseOSError(errno.EIO)

    def mkdir(self, path, mode):
        """Attempt to create a folder remotely if supported."""
        logger.debug(f"mkdir requested for {path}")
        parts = [p for p in path.split('/') if p]
        parent = '/' if len(parts) <= 1 else '/' + '/'.join(parts[:-1])
        name = parts[-1] if parts else ''
        try:
            parent_node = self._get_node(parent)
            if parent_node is NODE_NOT_FOUND:
                logger.debug(f"mkdir: parent {parent} not found remotely, falling back to local cache creation")
                parent_node = None
            # Try common folder creation methods
            if parent_node:
                for method in ('create_folder', 'mkdir', 'folder', 'make_folder'):
                    if hasattr(parent_node, method):
                        try:
                            getattr(parent_node, method)(name)
                            logger.info(f"Created remote folder {path} via {method}")

                            # Ensure local cache directory exists so getattr succeeds immediately
                            local_dir = os.path.join(self.root_cache, path.lstrip('/'))
                            try:
                                os.makedirs(local_dir, exist_ok=True)
                            except Exception as e:
                                logger.warning(f"Failed to create local cache dir for {path}: {e}")

                            # Pre-populate getattr cache
                            now = time.time()
                            attrs = dict(st_mode=(stat.S_IFDIR | 0o777), st_nlink=2, st_size=0, st_ctime=now, st_mtime=now, st_atime=now)
                            self.getattr_cache[path] = {'timestamp': now, 'attrs': attrs}

                            # update cache to include new folder
                            try:
                                if parent in self.dir_cache:
                                    children = set(self.dir_cache[parent].get('children', []))
                                    children.add(name)
                                    self.dir_cache[parent]['children'] = list(children)
                                    self.dir_cache[parent]['timestamp'] = time.time()
                                else:
                                    self.dir_cache[parent] = {'timestamp': time.time(), 'children': [name]}
                            except Exception:
                                pass
                            
                            # Clear negative cache so _get_node doesn't fail immediately
                            if path in self.negative_node_cache:
                                del self.negative_node_cache[path]
                                
                            return 0
                        except Exception as e:
                            logger.debug(f"parent.{method} failed: {e}")
            # Fallback: create local cache dir
            local_dir = os.path.join(self.root_cache, path.lstrip('/'))
            os.makedirs(local_dir, exist_ok=True)
            # update dir_cache for parent so readdir sees the new folder immediately
            try:
                if parent in self.dir_cache:
                    children = set(self.dir_cache[parent].get('children', []))
                    children.add(name)
                    self.dir_cache[parent]['children'] = list(children)
                    self.dir_cache[parent]['timestamp'] = time.time()
                else:
                    self.dir_cache[parent] = {'timestamp': time.time(), 'children': [name]}
            except Exception:
                pass
            return 0
        except FuseOSError:
            raise
        except Exception as e:
            logger.error(f"mkdir error for {path}: {e}")
            raise FuseOSError(errno.EIO)

    def rename(self, old, new):
        """Attempt remote rename if supported, otherwise adjust cache and mark upload."""
        logger.debug(f"rename requested from {old} to {new}")
        try:
            node = self._get_node(old)
            if node and node is not NODE_NOT_FOUND and hasattr(node, 'rename'):
                try:
                    node.rename(new)
                    logger.info(f"Renamed remote {old} -> {new}")
                    if new in self.negative_node_cache:
                        del self.negative_node_cache[new]
                    return 0
                except Exception as e:
                    logger.debug(f"Remote rename failed: {e}")

            # fallback: move local cache and mark new for upload
            old_local = os.path.join(self.root_cache, old.lstrip('/'))
            new_local = os.path.join(self.root_cache, new.lstrip('/'))
            if os.path.exists(old_local):
                os.makedirs(os.path.dirname(new_local), exist_ok=True)
                os.rename(old_local, new_local)
                self.pending_uploads[new] = new_local
                if old in self.pending_uploads:
                    del self.pending_uploads[old]
                if new in self.negative_node_cache:
                    del self.negative_node_cache[new]
            return 0
        except Exception as e:
            logger.error(f"rename error {old}->{new}: {e}")
            raise FuseOSError(errno.EIO)

def mount_daemon(api, mount_point, cache_dir, local_mappings=None):
    if not os.path.exists(mount_point):
        os.makedirs(mount_point)
    
    # foreground=True is easier for debugging, but blocks the thread.
    # In main.py we run this last.
    logger.info("Initializing FUSE driver...")
    
    # Silence verbose pyicloud warnings
    logging.getLogger("pyicloud").setLevel(logging.INFO)
    logging.getLogger("pyicloud.base").setLevel(logging.ERROR)
    
    fuse = FUSE(RealCloudFS(api, cache_dir, local_mappings), mount_point, foreground=True)