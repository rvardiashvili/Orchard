import os
import logging
import re
from collections import defaultdict

logger = logging.getLogger(__name__)

class LocalBrain:
    def __init__(self, sync_root):
        self.sync_root = sync_root
        self.index = defaultdict(list)
        
    def index_files(self):
        """
        Simple text indexer for user documents.
        """
        logger.info(f"Indexing Documents in {self.sync_root}...")
        count = 0
        try:
            # Use the path passed during init
            search_path = self.sync_root
            if not os.path.exists(search_path):
                logger.warning(f"Search path {search_path} does not exist.")
                return 0

            for root, dirs, files in os.walk(search_path):
                for file in files:
                    # Skip internal metadata files
                    if file in ['dir_structure.json', 'handoff.log'] or file.startswith('.'):
                        continue

                    # Index text-based files
                    if file.endswith(('.txt', '.md', '.py', '.log', '.html', '.json', '.xml', '.sh')):
                        path = os.path.join(root, file)
                        try:
                            # Use utf-8 and ignore errors to be robust
                            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                                content = f.read().lower()
                                
                                # Better Tokenization: Replace non-alphanumeric with spaces
                                # This splits "<h1>Title</h1>" into "h1", "title", "h1"
                                clean_content = re.sub(r'[^a-z0-9]', ' ', content)
                                
                                words = set(clean_content.split())
                                for w in words:
                                    if len(w) > 2: # Index words > 2 chars
                                        self.index[w].append(file)
                            count += 1
                        except Exception as e:
                            # logger.debug(f"Failed to read {file}: {e}")
                            pass
        except Exception as e:
            logger.error(f"Indexing failed: {e}")
            
        logger.info(f"Indexed {count} documents.")
        return count

    def query(self, term):
        """
        Returns files matching the term.
        """
        # Clean query
        term = re.sub(r'[^a-z0-9]', ' ', term.lower()).strip()
        
        # Exact match first
        results = self.index.get(term, [])
        
        # Partial match if no exact results (simple fuzzy)
        if not results:
            for key in self.index:
                if term in key:
                    results.extend(self.index[key])
                    
        # Deduplicate
        return list(set(results))

# Singleton placeholder
# In real usage, we'd init this with the path in main.py
brain = None
