from src.objects.base import OrchardObject

class Note(OrchardObject):
    def __init__(self, db, row=None):
        super().__init__(db, row)
        self.body = "" 
        self._load_body()
        # Calculate size immediately for stat()
        self._cached_bytes = self._to_bytes()
        self.size = len(self._cached_bytes)

    def _load_body(self):
        row = self.db.fetchone("SELECT local_path FROM drive_cache WHERE object_id = ?", (self.id,))
        if row and row['local_path']:
            try:
                with open(row['local_path'], 'r', encoding='utf-8') as f:
                    self.body = f.read()
            except:
                self.body = ""

    def _to_bytes(self):
        """Internal serializer."""
        content = f"---\nid: {self.id}\nmodified: {self.local_modified_at}\n---\n\n# {self.name}\n\n{self.body}"
        return content.encode('utf-8')

    def read(self, size, offset):
        # Use cached bytes calculated in init or update
        return self._cached_bytes[offset:offset+size]

    def write(self, data, offset):
        # Simple implementation: expect full write at offset 0
        if offset == 0:
            self._update_from_bytes(data)
            self.commit()
            return len(data)
        else:
            # Partial write on virtual file is hard without backing store logic.
            # Returning 0 or handling appropriately.
            return 0

    def _update_from_bytes(self, data: bytes):
        text = data.decode('utf-8')
        lines = text.split('\n')
        body_lines = []
        in_frontmatter = False
        for line in lines:
            if line.strip() == '---':
                in_frontmatter = not in_frontmatter
                continue
            if in_frontmatter: continue
            body_lines.append(line)
            
        full_text = '\n'.join(body_lines).strip()
        if full_text.startswith('# '):
            title_line = full_text.split('\n')[0]
            self.name = title_line[2:].strip()
            self.body = full_text[len(title_line):].strip()
        else:
            self.body = full_text
            
        self._save_body_to_cache()
        # Refresh cache
        self._cached_bytes = self._to_bytes()
        self.size = len(self._cached_bytes)

    def _save_body_to_cache(self):
        import os
        cache_dir = os.path.expanduser("~/.cache/orchard/blobs")
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, self.id)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.body)
        self.db.execute("INSERT OR REPLACE INTO drive_cache (object_id, local_path) VALUES (?, ?)", (self.id, path))