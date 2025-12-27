import logging
import threading

logger = logging.getLogger(__name__)

def start_tray(stop_event):
    """
    Tray icon is currently disabled due to library incompatibilities with the 
    virtual environment on Manjaro (missing system-level GI bindings).
    
    To enable:
    1. Install system dependencies: sudo pacman -S libappindicator-gtk3 python-gobject
    2. Re-create venv with system site packages.
    """
    logger.info("System Tray disabled to prevent crashes on this environment.")
    pass
