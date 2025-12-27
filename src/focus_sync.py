import logging
import threading
import subprocess
import time

logger = logging.getLogger(__name__)

class FocusSyncManager:
    """
    Manages synchronization of 'Do Not Disturb' / Focus Mode.
    Triggered via API or file watchers.
    """
    
    def __init__(self):
        self.is_dnd_active = False
        
    def set_focus(self, state: bool):
        """
        Toggles Linux DND based on state (True=ON, False=OFF).
        """
        if self.is_dnd_active == state:
            return # No change
            
        self.is_dnd_active = state
        
        logger.info(f"Setting Focus Mode: {'ON' if state else 'OFF'}")
        
        # 1. Dunst (Common on i3/Manjaro)
        try:
            cmd = "set-paused"
            arg = "true" if state else "false"
            subprocess.run(["dunstctl", cmd, arg], check=False)
            logger.info(f"Executed: dunstctl {cmd} {arg}")
        except Exception:
            pass 
            
        # 2. GNOME (gsettings)
        try:
            # show-banners false = DND ON
            val = "false" if state else "true"
            subprocess.run(["gsettings", "set", "org.gnome.desktop.notifications", "show-banners", val], check=False)
        except Exception:
            pass

        # 3. KDE Plasma (Try to set config - requires restart of plasmashell often, but worth a shot)
        # Skipping complex KDE DBus calls as they vary by version.
        
        # 4. Fallback Visual Feedback (using notify-send before muting or after unmuting)
        if not state: # If turning DND OFF, notify
            try:
                subprocess.run(["notify-send", "UnixSync", "Focus Mode Disabled"], check=False)
            except: pass
        else: # If turning ON, try to notify just before
            try:
                subprocess.run(["notify-send", "UnixSync", "Focus Mode Enabled (Muting)"], check=False)
            except: pass

    def handle_api_request(self, data):
        """
        Handles POST /api/v1/focus
        payload: {"state": "on"} or {"state": "off"} or {"state": true/false}
        """
        raw_state = data.get('state')
        state_bool = False
        
        if isinstance(raw_state, bool):
            state_bool = raw_state
        else:
            state_str = str(raw_state).lower()
            if state_str in ['on', 'true', '1']:
                state_bool = True
            elif state_str in ['off', 'false', '0']:
                state_bool = False
        
        self.set_focus(state_bool)
            
        return {"status": "success", "dnd": self.is_dnd_active}

focus_manager = FocusSyncManager()
