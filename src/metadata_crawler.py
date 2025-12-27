import threading
import time
import json
import os
import logging

logger = logging.getLogger(__name__)

class MetadataCrawler:
    def __init__(self, api, cache_dir, interval=300):
        self.api = api
        self.cache_dir = cache_dir
        self.map_file = os.path.join(cache_dir, "cloud_map.json")
        self.interval = interval
        self.file_map = []
        self._running = False

    def start(self):
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        logger.info("Metadata Crawler started.")
        while self._running:
            try:
                self.refresh_map()
            except Exception as e:
                logger.error(f"Crawler failed: {e}")
            
            time.sleep(self.interval)

    def refresh_map(self):
        logger.info("Crawling iCloud Directory Structure...")
        new_map = []
        
        # Recursive walker
        def walk(node, path=""):
            # Determine if node is a folder or file
            # Root drive doesn't have .type, but has .dir()
            # Files have .type='file'
            
            node_type = getattr(node, 'type', 'folder') # Default to folder for root
            
            if node_type == 'file':
                # It's a file, don't descend
                return

            try:
                children = node.dir()
                for name in children:
                    child = node[name]
                    child_path = os.path.join(path, name)
                    
                    child_type = getattr(child, 'type', 'unknown')
                    is_dir = (child_type == 'folder')
                    
                    item = {
                        "path": child_path,
                        "name": name,
                        "type": child_type,
                        "size": getattr(child, 'size', 0)
                    }
                    new_map.append(item)
                    
                    if is_dir:
                        walk(child, child_path)
            except Exception as e:
                # If dir() fails, it might be a file misidentified or permission issue
                # logger.warning(f"Failed to crawl {path}: {e}")
                pass

        # Start from root
        walk(self.api.drive)
        
        # Atomic update
        self.file_map = new_map
        self._save_map()
        logger.info(f"Crawl Complete. Mapped {len(new_map)} items.")

    def _save_map(self):
        try:
            with open(self.map_file, 'w') as f:
                json.dump(self.file_map, f)
        except Exception as e:
            logger.error(f"Failed to save cloud map: {e}")

    def search(self, query):
        """
        Searches the memory map for filenames matching query.
        """
        query = query.lower()
        results = []
        for item in self.file_map:
            if query in item['name'].lower():
                results.append(item['path'])
        return results

# Global instance
crawler = None
