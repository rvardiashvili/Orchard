import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib

class OrchardWindow(Gtk.Window):
    def __init__(self, engine, mount_point):
        super().__init__(title="Orchard Control Panel")
        self.engine = engine
        self.mount_point = mount_point
        
        self.set_border_width(10)
        self.set_default_size(500, 400)
        self.set_position(Gtk.WindowPosition.CENTER)
        
        # Header Bar
        header = Gtk.HeaderBar(title="Orchard")
        header.set_show_close_button(True)
        header.props.subtitle = "iCloud for Linux"
        self.set_titlebar(header)
        
        # Tabs
        notebook = Gtk.Notebook()
        self.add(notebook)
        
        # Tab 1: Status
        self.status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.status_box.set_border_width(20)
        self._init_status_tab()
        notebook.append_page(self.status_box, Gtk.Label(label="Status"))
        
        # Tab 2: Conflicts
        self.conflict_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.conflict_box.set_border_width(20)
        self._init_conflict_tab()
        notebook.append_page(self.conflict_box, Gtk.Label(label="Conflicts"))
        
        # Tab 3: Settings
        self.settings_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.settings_box.set_border_width(20)
        self._init_settings_tab()
        notebook.append_page(self.settings_box, Gtk.Label(label="Settings"))
        
        # Timer for refreshing UI
        GLib.timeout_add_seconds(2, self._refresh_ui)

    def _init_status_tab(self):
        # Account
        self.lbl_account = Gtk.Label(label=f"Account: {self.engine.api.apple_id}", xalign=0)
        self.status_box.pack_start(self.lbl_account, False, False, 0)
        
        # Mount
        lbl_mount = Gtk.Label(label=f"Mount Point: {self.mount_point}", xalign=0)
        self.status_box.pack_start(lbl_mount, False, False, 0)
        
        # Dynamic Status
        self.lbl_state = Gtk.Label(label="State: Checking...", xalign=0)
        self.status_box.pack_start(self.lbl_state, False, False, 0)
        
        # Pending Counts
        self.lbl_pending = Gtk.Label(label="Pending Actions: 0", xalign=0)
        self.status_box.pack_start(self.lbl_pending, False, False, 0)

    def _init_conflict_tab(self):
        lbl = Gtk.Label(label="Conflicting Files", xalign=0)
        self.conflict_box.pack_start(lbl, False, False, 0)
        
        # Scrolled List
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        self.conflict_box.pack_start(scrolled, True, True, 0)
        
        self.conflict_list = Gtk.ListBox()
        self.conflict_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled.add(self.conflict_list)
        
        # Refresh button
        btn_refresh = Gtk.Button(label="Refresh Conflicts")
        btn_refresh.connect("clicked", self._load_conflicts)
        self.conflict_box.pack_start(btn_refresh, False, False, 0)

    def _init_settings_tab(self):
        lbl = Gtk.Label(label="Configuration", xalign=0)
        self.settings_box.pack_start(lbl, False, False, 0)
        
        # Placeholder for future settings
        check_login = Gtk.CheckButton(label="Start Orchard on Login")
        check_login.set_sensitive(False) # Not implemented yet
        self.settings_box.pack_start(check_login, False, False, 0)
        
        btn_reset = Gtk.Button(label="Reset Cache & Restart")
        btn_reset.set_sensitive(False) 
        self.settings_box.pack_start(btn_reset, False, False, 0)

    def _refresh_ui(self):
        # Update Status
        if not self.engine.drive_svc:
            self.lbl_state.set_markup("State: <b>OFFLINE</b>")
        else:
            self.lbl_state.set_markup("State: <b>Online</b>")
            
        pending = self.engine.db.fetchone("SELECT COUNT(*) as c FROM actions WHERE status IN ('pending', 'processing')")
        count = pending['c'] if pending else 0
        self.lbl_pending.set_label(f"Pending Actions: {count}")
        
        # Auto-refresh conflicts if visible?
        # For now, rely on manual refresh or periodic if empty
        return True

    def _load_conflicts(self, _=None):
        # Clear list
        for child in self.conflict_list.get_children():
            self.conflict_list.remove(child)
            
        # Query conflicts
        rows = self.engine.db.fetchall("SELECT * FROM objects WHERE sync_state='conflict'")
        
        if not rows:
            lbl = Gtk.Label(label="No conflicts found.")
            self.conflict_list.add(lbl)
        
        for row in rows:
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            
            lbl_name = Gtk.Label(label=row['name'])
            row_box.pack_start(lbl_name, True, True, 0)
            
            btn_local = Gtk.Button(label="Keep Local")
            btn_local.get_style_context().add_class("suggested-action")
            btn_local.connect("clicked", self._resolve_keep_local, row['id'])
            row_box.pack_start(btn_local, False, False, 0)
            
            btn_cloud = Gtk.Button(label="Keep Cloud")
            btn_cloud.get_style_context().add_class("destructive-action")
            btn_cloud.connect("clicked", self._resolve_keep_cloud, row['id'])
            row_box.pack_start(btn_cloud, False, False, 0)
            
            self.conflict_list.add(row_box)
        
        self.conflict_list.show_all()

    def _resolve_keep_local(self, btn, obj_id):
        # Force Push: Mark dirty, set state to pending_push
        print(f"Resolving {obj_id}: Keep Local")
        self.engine.db.execute("UPDATE objects SET sync_state='pending_push', dirty=1 WHERE id=?", (obj_id,))
        self.engine.db.enqueue_action(obj_id, 'update_content', 'push', priority=20)
        self._load_conflicts()

    def _resolve_keep_cloud(self, btn, obj_id):
        # Force Pull: Mark clean, set state to pending_pull (or ensure_latest)
        print(f"Resolving {obj_id}: Keep Cloud")
        # We should delete local cache to force re-download?
        # Or just download over it.
        self.engine.db.execute("UPDATE objects SET sync_state='pending_pull', dirty=0 WHERE id=?", (obj_id,))
        # Set present=0 (Missing) to force download
        self.engine.db.execute("UPDATE drive_cache SET present_locally=0 WHERE object_id=?", (obj_id,))
        
        self.engine.db.enqueue_action(obj_id, 'ensure_latest', 'pull', priority=20)
        self._load_conflicts()
