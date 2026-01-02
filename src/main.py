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

print("DEBUG: Module Loading...")

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.icloud_client.client import OrchardiCloudClient
from src.db.orchardDB import get_db
from src.sync.engine import SyncEngine
from src.fs.orchardFS import mount_daemon
from src.config.manager import ConfigManager

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
    print("DEBUG: Entering main()")
    parser = argparse.ArgumentParser(description="Orchard - iCloud Sync Engine")
    parser.add_argument("--apple-id", required=False, help="Apple ID (Overrides config)")
    parser.add_argument("--mount-point", required=False, help="Mount point (Overrides config)")
    parser.add_argument("--db-path", default=os.path.expanduser("~/.local/share/orchard/orchard.db"))
    parser.add_argument("--cookie-dir", default=os.path.expanduser("~/.local/share/orchard/icloud_session"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # 0. Load Config & Wizard
    config = ConfigManager()
    print(f"DEBUG: Config Loaded. AppleID={config.apple_id}")
    
    if args.apple_id: config.set("apple_id", args.apple_id)
    if args.mount_point: config.set("mount_point", args.mount_point)
    
    if not config.apple_id or not config.mount_point:
        logger.info("Configuration missing. Launching Setup Wizard...")
        try:
            from src.gui.wizard import run_wizard
            run_wizard()
            # If wizard completed, config should be saved.
            if not config.apple_id: # User cancelled
                logger.info("Setup cancelled.")
                sys.exit(0)
        except Exception as e:
            logger.error(f"Failed to run wizard: {e}")
            sys.exit(1)

    # 1. DB
    orchard_db = get_db(config.db_path)

    # 2. Auth
    client = OrchardiCloudClient(config.apple_id, cookie_directory=config.cookie_dir)
    
    print("DEBUG: Checking connection...")
    if check_connection():
        logger.info("Internet connection detected. Authenticating...")
        client.authenticate() 
        if not client.authenticated:
            logger.error("Authentication failed. Please re-run setup or check credentials.")
            pass
    else:
        logger.warning("No internet connection. Starting in OFFLINE Mode.")

    print("DEBUG: Starting Engine...")
    # 3. Engine
    engine = SyncEngine(orchard_db, client)
    threading.Thread(target=engine.start, daemon=True).start()

    # 4. FUSE (Thread)
    print("DEBUG: Starting FUSE...")
    mount_point = config.mount_point
    if not os.path.exists(mount_point): os.makedirs(mount_point)
    logger.info(f"Mounting {mount_point}...")
    
    fuse_thread = threading.Thread(target=mount_daemon, args=(config.db_path, mount_point))
    fuse_thread.daemon = True
    fuse_thread.start()

    # 5. GUI / Main Loop
    print("DEBUG: Starting GUI...")
    try:
        from src.gui.tray import OrchardTray
        logger.info("Starting System Tray Icon...")
        tray = OrchardTray(engine, mount_point)
        # Enable Ctrl+C support for Gtk
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        tray.run()
    except (ImportError, ValueError) as e:
        logger.warning(f"GUI Unavailable ({e}). Running in Headless Mode.")
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        logger.info("Stopping...")
        engine.stop()
        os.system(f"fusermount -u -z {mount_point}")

def show_error_dialog(error_msg):
    """Try to show a native error dialog using system tools."""
    import subprocess
    import shutil
    msg = f"Orchard failed to start:\n\n{error_msg}\n\nSee ~/.cache/orchard/orchard.log for details."
    
    try:
        if shutil.which("zenity"):
            subprocess.run(["zenity", "--error", "--text", msg, "--title", "Orchard Error"])
        elif shutil.which("kdialog"):
            subprocess.run(["kdialog", "--error", msg, "--title", "Orchard Error"])
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", "-u", "critical", "Orchard Error", msg])
        elif shutil.which("xmessage"):
            subprocess.run(["xmessage", "-center", msg])
    except:
        pass # If this fails, we can't do much

if __name__ == "__main__":
    # Ensure log directory exists
    log_dir = os.path.expanduser("~/.cache/orchard")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "orchard.log")
    
    # Configure File Logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )

    try:
        main()
    except Exception as e:
        logger.critical("Fatal Error", exc_info=True)
        show_error_dialog(str(e))
        sys.exit(1)