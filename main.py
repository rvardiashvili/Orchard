import threading
import time
import logging
from src.auth import AuthManager
from src.api_server import start_server
from src.vfs import mount_daemon
from src.tray import start_tray # Import Tray
from src.metadata_crawler import MetadataCrawler, crawler # Import Crawler
from src.watchers import start_watcher
from src.services_sync import sync_calendar, sync_reminders, export_contacts, sync_notes
import argparse
import os
import sys
import yaml


# Configure Logging
logging.basicConfig(
    level=logging.INFO, 
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("Orchard")

def load_config(config_path="config/settings.yaml"):
    if not os.path.exists(config_path):
        return {}
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

# ... (omitted code) ...

def main():
    parser = argparse.ArgumentParser(description="Orchard: iCloud Sync Service for Linux")
    parser.add_argument("--config", default="config/settings.yaml", help="Path to configuration file")
    parser.add_argument("--login", action="store_true", help="Perform login and 2FA")
    parser.add_argument("--mount", help="Mount point for iCloud Drive (overrides config)")
    parser.add_argument("--headless", action="store_true", help="Run without System Tray icon")
    
    args = parser.parse_args()
    config = load_config(args.config)
    
    # 1. Authentication
    username = config.get("username")
    if not username:
        username = input("Please enter your iCloud username (email): ")
        config['username'] = username # Potentially save to config later or just use for this run
    
    auth_mgr = AuthManager(username)
    api = None
    
    if args.login:
        logger.info("Initiating login sequence...")
        api = auth_mgr.login()
        logger.info(f"Successfully logged in as: {api.user.get('fullName')}")
    else:
        try:
            api = auth_mgr.get_service()
        except Exception as e:
            logger.error(f"Auth Error: {e}")
            logger.error("Please run with --login first.")
            sys.exit(1)

    # 2. Start Local API Bridge (Non-blocking)
    sync_root = args.mount or config.get("sync_root")
    # Expand user
    if sync_root.startswith("~"):
        sync_root = os.path.expanduser(sync_root)

    # Make sure cache dir exists
    cache_dir = config.get("cache_dir", "~/.cache/icloud_sync")
    if cache_dir.startswith("~"):
        cache_dir = os.path.expanduser(cache_dir)
    os.makedirs(cache_dir, exist_ok=True)
    
    # 2.5 Start Metadata Crawler (The Map Builder)
    import src.metadata_crawler
    logger.info("Starting Cloud Filesystem Crawler...")
    src.metadata_crawler.crawler = MetadataCrawler(api, cache_dir)
    src.metadata_crawler.crawler.start()
    
    # 2.6 Start Filesystem Watcher (Handoff/Clipboard)
    watcher = start_watcher(cache_dir)

    # Start API with the Real API Client (or None if offline)
    start_server(sync_root, api, port=8080)

    # 3. Start Hardware Bridges
    run_hardware_integrations()
    
    # 3.1 Start Service Sync Loop
    threading.Thread(target=service_sync_loop, args=(api, cache_dir), daemon=True).start()
    
    # 3.5 Start System Tray (if not headless)
    stop_event = threading.Event()
    if not args.headless:
        logger.info("Starting System Tray Icon...")
        start_tray(stop_event)

    # 4. Mount Filesystem (Blocking)
    if sync_root:
        logger.info(f"Mounting iCloud Drive at: {sync_root}")
        logger.info(f"Local Cache: {cache_dir}")
        logger.info("Web Dashboard: http://localhost:8080")
        logger.info("Press Ctrl+C to stop.")
        
        # Parse Mapped Folders
        raw_mappings = config.get("mapped_folders", {})
        mappings = {}
        if raw_mappings:
            for k, v in raw_mappings.items():
                mappings[k] = os.path.expanduser(v)
            logger.info(f"Active Folder Mappings: {list(mappings.keys())}")
        
        try:
            mount_daemon(api, sync_root, cache_dir, mappings)
        except KeyboardInterrupt:
            logger.info("\nStopping services...")
            sys.exit(0)
        except Exception as e:
            logger.error(f"FUSE Error: {e}")
            logger.info(f"Try running: fusermount -u {sync_root}")
    else:
        logger.error("No sync root defined in config/settings.yaml")

if __name__ == "__main__":
    main()
