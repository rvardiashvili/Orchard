import json
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(os.path.expanduser("~/.config/orchard"))
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "apple_id": None,
    "mount_point": str(Path.home() / "iCloud"),
    "db_path": str(Path.home() / ".local/share/orchard/orchard.db"),
    "cookie_dir": str(Path.home() / ".local/share/orchard/icloud_session"),
    "auto_start": False
}

class ConfigManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        self._config = DEFAULT_CONFIG.copy()
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    self._config.update(data)
            except Exception as e:
                logger.error(f"Failed to load config: {e}")

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self._config, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def get(self, key):
        return self._config.get(key)

    def set(self, key, value):
        self._config[key] = value
        self.save()

    @property
    def apple_id(self): return self.get("apple_id")
    
    @property
    def mount_point(self): return self.get("mount_point")
    
    @property
    def db_path(self): return self.get("db_path")
    
    @property
    def cookie_dir(self): return self.get("cookie_dir")
