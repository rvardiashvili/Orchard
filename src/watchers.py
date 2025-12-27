import os
import time
import threading
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import subprocess

logger = logging.getLogger(__name__)

class SyncWatcher(FileSystemEventHandler):
    """
    Watches local cache/sync folders for changes to trigger system actions.
    """
    def __init__(self, root_path):
        self.root_path = root_path
        self.handoff_file = os.path.join(root_path, "LinuxSync", "handoff.log")
        self.clipboard_file = os.path.join(root_path, ".clipboard")
        self.cmd_dir = os.path.join(root_path, "LinuxSync", "Commands")

    def on_modified(self, event):
        if event.is_directory:
            return
            
        # Handoff (URL Opener)
        if event.src_path.endswith("handoff.log") or event.src_path == self.handoff_file:
            self._process_handoff()

        # Clipboard
        if event.src_path.endswith(".clipboard") or event.src_path == self.clipboard_file:
            self._process_clipboard()

    def on_created(self, event):
        # Command Trigger (e.g. lock_screen file created)
        if self.cmd_dir in event.src_path:
            filename = os.path.basename(event.src_path)
            self._process_command(filename)

    def _process_handoff(self):
        try:
            # Read last line
            with open(self.handoff_file, 'r') as f:
                lines = f.readlines()
                if lines:
                    url = lines[-1].strip()
                    if url.startswith("http"):
                        logger.info(f"Handoff Trigger: Opening {url}")
                        # Open in default browser
                        subprocess.run(["xdg-open", url], check=False)
        except Exception as e:
            logger.error(f"Handoff failed: {e}")

    def _process_clipboard(self):
        try:
            with open(self.clipboard_file, 'r') as f:
                text = f.read()
                if text:
                    logger.info("Clipboard Trigger: Updating System Clipboard")
                    # Try xclip (X11) or wl-copy (Wayland)
                    # We try both
                    try:
                        p = subprocess.Popen(['xclip', '-selection', 'clipboard'], stdin=subprocess.PIPE)
                        p.communicate(input=text.encode('utf-8'))
                    except FileNotFoundError:
                        try:
                            p = subprocess.Popen(['wl-copy'], stdin=subprocess.PIPE)
                            p.communicate(input=text.encode('utf-8'))
                        except FileNotFoundError:
                            logger.warning("No clipboard utility found (install xclip or wl-clipboard)")
        except Exception as e:
            logger.error(f"Clipboard sync failed: {e}")

    def _process_command(self, filename):
        logger.info(f"Command Trigger: {filename}")
        if "lock" in filename:
            subprocess.run(["loginctl", "lock-session"])
        # Remove file after execution
        try:
            os.remove(os.path.join(self.cmd_dir, filename))
        except: pass

def start_watcher(sync_root):
    # Ensure dirs exist
    handoff_dir = os.path.join(sync_root, "LinuxSync")
    cmd_dir = os.path.join(handoff_dir, "Commands")
    os.makedirs(cmd_dir, exist_ok=True)
    
    event_handler = SyncWatcher(sync_root)
    observer = Observer()
    observer.schedule(event_handler, sync_root, recursive=True)
    observer.start()
    logger.info(f"Filesystem Watcher active on {sync_root}")
    return observer
