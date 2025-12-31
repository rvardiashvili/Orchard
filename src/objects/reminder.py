import json
import os
from src.objects.base import OrchardObject

class ReminderList(OrchardObject):
    def __init__(self, db, row=None):
        super().__init__(db, row)
        self.tasks = []
        self._load_tasks()
        self._cached_bytes = self._to_bytes()
        self.size = len(self._cached_bytes)

    def _load_tasks(self):
        row = self.db.fetchone("SELECT local_path FROM drive_cache WHERE object_id = ?", (self.id,))
        if row and row['local_path']:
            try:
                with open(row['local_path'], 'r', encoding='utf-8') as f:
                    self.tasks = json.load(f)
            except:
                self.tasks = []

    def _to_bytes(self):
        lines = [f"# {self.name}", ""]
        for t in self.tasks:
            chk = "[x]" if t.get('completed') else "[ ]"
            lines.append(f"- {chk} {t.get('title', 'Task')}")
        return "\n".join(lines).encode('utf-8')

    def read(self, size, offset):
        return self._cached_bytes[offset:offset+size]

    def write(self, data, offset):
        if offset == 0:
            self._update_from_bytes(data)
            self.commit()
            return len(data)
        return 0

    def _update_from_bytes(self, data: bytes):
        text = data.decode('utf-8')
        lines = text.split('\n')
        new_tasks = []
        for line in lines:
            line = line.strip()
            if line.startswith('- [ ]'):
                new_tasks.append({'title': line[5:].strip(), 'completed': False})
            elif line.startswith('- [x]'):
                new_tasks.append({'title': line[5:].strip(), 'completed': True})
            elif line.startswith('# '):
                self.name = line[2:].strip()
        self.tasks = new_tasks
        self._save_tasks()
        self._cached_bytes = self._to_bytes()
        self.size = len(self._cached_bytes)

    def _save_tasks(self):
        cache_dir = os.path.expanduser("~/.cache/orchard/blobs")
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, self.id)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.tasks, f)
        self.db.execute("INSERT OR REPLACE INTO drive_cache (object_id, local_path) VALUES (?, ?)", (self.id, path))