import argparse
import logging
import os
import signal
import sys
import threading
import time
import socket
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.icloud_client.client import OrchardiCloudClient
from src.db.orchardDB import get_db
from src.sync.engine import SyncEngine
from src.fs.orchardFS import mount_daemon

logger = logging.getLogger(__name__)

def check_connection(host="1.1.1.1", port=53, timeout=3):
    """Check for internet connectivity."""
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port))
        return True
    except socket.error:
        return False

def main():
    parser = argparse.ArgumentParser(description="Orchard - Drive Sync Only")
    parser.add_argument("--apple-id", required=True)
    parser.add_argument("--mount-point", required=True)
    parser.add_argument("--db-path", default=os.path.expanduser("~/.local/share/orchard/orchard.db"))
    parser.add_argument("--cookie-dir", default=os.path.expanduser("~/.local/share/orchard/icloud_session"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # 1. DB
    orchard_db = get_db(args.db_path)

    # 2. Auth
    client = OrchardiCloudClient(args.apple_id, cookie_directory=args.cookie_dir)
    
    if check_connection():
        logger.info("Internet connection detected. Authenticating...")
        client.authenticate()
        if not client.authenticated:
            logger.error("Authentication failed. Please check your credentials.")
            sys.exit(1)
    else:
        logger.warning("No internet connection. Starting in OFFLINE Mode.")

    # 3. Engine
    engine = SyncEngine(orchard_db, client)
    threading.Thread(target=engine.start, daemon=True).start()

    # 4. FUSE
    if not os.path.exists(args.mount_point): os.makedirs(args.mount_point)
    
    logger.info(f"Mounting {args.mount_point}...")
    try:
        mount_daemon(args.db_path, args.mount_point)
    except KeyboardInterrupt:
        logger.info("Stopping...")
        engine.stop()
        os.system(f"fusermount -u {args.mount_point}")

if __name__ == "__main__":
    main()