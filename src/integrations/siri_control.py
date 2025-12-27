import logging
import os
import subprocess

logger = logging.getLogger(__name__)

def handle_command(command, args=None):
    """
    Executes a predefined Siri command.
    """
    cmd = command.lower()
    
    if cmd == "lock":
        # Supports GNOME, KDE, i3 (via loginctl)
        try:
            logger.info("Siri Command: Locking Session")
            subprocess.run(["loginctl", "lock-session"], check=True)
            return {"status": "success", "message": "Session Locked"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    elif cmd == "update":
        # Runs system update (requires passwordless sudo or polkit agent)
        # For safety, we just launch the terminal
        try:
            # Detect terminal emulator?
            term = os.environ.get("TERM", "xterm")
            logger.info("Siri Command: Opening Update Terminal")
            # This is tricky without display context, might need 'export DISPLAY=:0'
            # Simpler action: Just touch a file that a user script watches?
            # Or use `pkexec`
            return {"status": "success", "message": "Update command received (Not fully implemented for safety)"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
            
    elif cmd == "backup":
        # Trigger the Time Machine Lite
        # We need to import dynamically to avoid circular deps if any
        from src.backup import backup_dotfiles
        # We need the sync root. This module needs context.
        # For now, we return a signal to the caller.
        return {"status": "signal", "signal": "backup"}

    return {"status": "error", "message": "Unknown command"}
