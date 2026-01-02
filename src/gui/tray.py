import gi
import threading
import time
import webbrowser
import os
import signal

try:
    gi.require_version('Gtk', '3.0')
    gi.require_version('AppIndicator3', '0.1')
    from gi.repository import Gtk, AppIndicator3, GLib
    from .window import OrchardWindow
except ValueError:
    print("CRITICAL: Gtk3 or AppIndicator3 not found. Tray icon will not work.")
    print("Install: sudo apt install gir1.2-appindicator3-0.1 (Ubuntu) or libappindicator-gtk3 (Fedora)")
    # We might want to fallback to CLI-only mode here, but let's assume dependencies for now.

class OrchardTray:
    def __init__(self, engine, mount_point):
        self.engine = engine
        self.mount_point = mount_point
        self.app_id = "orchard-sync"
        self.window = None
        
        # Paths
        self.icon_path = os.path.abspath("src/assets/icons/orchard-logo.svg")
        if not os.path.exists(self.icon_path):
            # Fallback to system icon or generic
            self.icon_path = "folder-remote" 

        self.indicator = AppIndicator3.Indicator.new(
            self.app_id,
            self.icon_path,
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        
        self.menu = Gtk.Menu()
        self._build_menu()
        self.indicator.set_menu(self.menu)
        
        # Start Status Polling (1 second)
        GLib.timeout_add_seconds(1, self._update_status)

    def _build_menu(self):
        # Status Label (Disabled Item)
        self.status_item = Gtk.MenuItem(label="Status: Starting...")
        self.status_item.set_sensitive(False)
        self.menu.append(self.status_item)
        
        # Divider
        self.menu.append(Gtk.SeparatorMenuItem())
        
        # Open Drive
        item_open = Gtk.MenuItem(label="Open Drive Folder")
        item_open.connect("activate", self._open_drive)
        self.menu.append(item_open)
        
        # Control Panel
        item_settings = Gtk.MenuItem(label="Control Panel")
        item_settings.connect("activate", self._open_settings)
        self.menu.append(item_settings)
        
        # Sync Now
        item_sync = Gtk.MenuItem(label="Sync Now (Refresh)")
        item_sync.connect("activate", self._sync_now)
        self.menu.append(item_sync)
        
        # Divider
        self.menu.append(Gtk.SeparatorMenuItem())
        
        # Quit
        item_quit = Gtk.MenuItem(label="Quit Orchard")
        item_quit.connect("activate", self._quit)
        self.menu.append(item_quit)
        
        self.menu.show_all()

    def _update_status(self):
        # Poll DB or Engine for status
        # Ideally engine exposes a quick property
        
        current_icon = "orchard-logo"
        
        # We can query the DB directly for pending actions count
        try:
            # Check Offline
            if not self.engine.drive_svc:
                self.status_item.set_label("Status: Offline (Connecting...)")
                self.indicator.set_icon("orchard-logo-offline")
                return True

            pending = self.engine.db.fetchone("SELECT COUNT(*) as c FROM actions WHERE status IN ('pending', 'processing')")
            count = pending['c'] if pending else 0
            
            failed = self.engine.db.fetchone("SELECT COUNT(*) as c FROM actions WHERE status='failed'")
            fail_count = failed['c'] if failed else 0
            
            if fail_count > 0:
                self.status_item.set_label(f"Status: {fail_count} Errors")
                current_icon = "orchard-logo-error"
            elif count > 0:
                self.status_item.set_label(f"Status: Syncing ({count} items)...")
                current_icon = "orchard-logo-sync"
            else:
                self.status_item.set_label("Status: Idle (Synced)")
                current_icon = "orchard-logo"
                
            self.indicator.set_icon(current_icon)
                
        except Exception as e:
            self.status_item.set_label("Status: Database Error")
            self.indicator.set_icon("orchard-logo-error")
            print(f"Tray Update Error: {e}")

        return True # Keep polling

    def _open_drive(self, _):
        try:
            # Cross-platform open
            if os.path.isdir(self.mount_point):
                subprocess.Popen(["xdg-open", self.mount_point])
        except Exception as e:
            print(f"Failed to open drive: {e}")

    def _open_settings(self, _):
        if self.window:
            self.window.present()
        else:
            self.window = OrchardWindow(self.engine, self.mount_point)
            self.window.connect("destroy", self._on_window_destroy)
            self.window.show_all()
            
    def _on_window_destroy(self, w):
        self.window = None

    def _sync_now(self, _):
        # Trigger metadata pull
        self.engine.db.enqueue_action('drive_root', 'list_children', 'pull', priority=10)
        self._update_status()

    def _quit(self, _):
        print("Quitting via Tray...")
        Gtk.main_quit()
        # The main.py should handle cleanup after Gtk loop ends

    def run(self):
        Gtk.main()

import subprocess # Forgot to import earlier
