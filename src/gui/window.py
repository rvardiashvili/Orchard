import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, GdkPixbuf
import os
import sys
from pathlib import Path

class OrchardWindow(Gtk.Window):
    def __init__(self, engine, mount_point):
        super().__init__(title="Orchard Control Panel")
        self.engine = engine
        self.mount_point = mount_point
        
        self.set_border_width(0) # Cleaner look
        self.set_default_size(650, 550)
        self.set_position(Gtk.WindowPosition.CENTER)
        
        # Header Bar
        header = Gtk.HeaderBar(title="Orchard")
        header.set_show_close_button(True)
        header.props.subtitle = "iCloud for Linux"
        self.set_titlebar(header)
        
        # About Button in HeaderBar
        btn_about = Gtk.Button.new_from_icon_name("help-about", Gtk.IconSize.MENU)
        btn_about.set_tooltip_text("About Orchard")
        btn_about.connect("clicked", self._on_about_dialog)
        header.pack_end(btn_about)
        
        # Window Icon
        try:
            self.assets_path = Path(__file__).parent.parent.parent / "src/assets/icons"
            icon_path = self.assets_path / "orchard-logo.svg"
            if icon_path.exists():
                self.set_icon_from_file(str(icon_path))
        except: pass
        
        # Main Layout
        self.notebook = Gtk.Notebook()
        self.add(self.notebook)
        
        # Tabs
        self._add_tab("Status", "network-idle", self._init_status_tab)
        self._add_tab("Conflicts", "dialog-warning", self._init_conflict_tab)
        self._add_tab("Settings", "preferences-system", self._init_settings_tab)
        
        # Timer for refreshing UI
        GLib.timeout_add_seconds(2, self._refresh_ui)

    def _add_tab(self, label, icon_name, init_func):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_border_width(20)
        init_func(box)
        
        # Tab Label with Icon
        tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        try:
            icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
            tab_box.pack_start(icon, False, False, 0)
        except: pass
        
        lbl = Gtk.Label(label=label)
        tab_box.pack_start(lbl, False, False, 0)
        tab_box.show_all()
        
        self.notebook.append_page(box, tab_box)

    def _init_status_tab(self, box):
        # Center Box for Logo & Status
        center_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        center_box.set_valign(Gtk.Align.CENTER)
        center_box.set_halign(Gtk.Align.CENTER)
        center_box.set_vexpand(True)
        box.pack_start(center_box, True, True, 0)

        # 1. Logo
        try:
            logo_path = self.assets_path / "orchard-logo.svg"
            if logo_path.exists():
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(logo_path), 128, 128, True)
                img_logo = Gtk.Image.new_from_pixbuf(pixbuf)
                center_box.pack_start(img_logo, False, False, 10)
        except Exception as e:
            print(f"Logo error: {e}")

        # 2. Status Label (Large)
        self.lbl_state = Gtk.Label()
        self.lbl_state.set_markup("<span size='xx-large' weight='bold'>Checking...</span>")
        center_box.pack_start(self.lbl_state, False, False, 10)
        
        # 3. Details Frame
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        frame.set_halign(Gtk.Align.CENTER)
        center_box.pack_start(frame, False, False, 20)
        
        grid = Gtk.Grid()
        grid.set_column_spacing(20)
        grid.set_row_spacing(10)
        grid.set_border_width(20)
        frame.add(grid)
        
        # Apple ID
        grid.attach(Gtk.Label(label="<b>Apple ID:</b>", use_markup=True, xalign=1), 0, 0, 1, 1)
        self.lbl_account = Gtk.Label(label=self.engine.api.apple_id, xalign=0)
        grid.attach(self.lbl_account, 1, 0, 1, 1)
        
        # Mount Point
        grid.attach(Gtk.Label(label="<b>Mount Point:</b>", use_markup=True, xalign=1), 0, 1, 1, 1)
        lbl_mount = Gtk.Label(label=self.mount_point, xalign=0)
        grid.attach(lbl_mount, 1, 1, 1, 1)
        
        # Pending Items
        grid.attach(Gtk.Label(label="<b>Pending Items:</b>", use_markup=True, xalign=1), 0, 2, 1, 1)
        self.lbl_pending = Gtk.Label(label="0", xalign=0)
        grid.attach(self.lbl_pending, 1, 2, 1, 1)

    def _init_conflict_tab(self, box):
        top_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon_warn = Gtk.Image.new_from_icon_name("dialog-warning", Gtk.IconSize.LARGE_TOOLBAR)
        top_box.pack_start(icon_warn, False, False, 0)
        lbl = Gtk.Label(label="Resolve synchronization conflicts below.")
        top_box.pack_start(lbl, False, False, 0)
        box.pack_start(top_box, False, False, 0)
        
        # Scrolled List
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        box.pack_start(scrolled, True, True, 0)
        
        self.conflict_list = Gtk.ListBox()
        self.conflict_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled.add(self.conflict_list)
        
        # Refresh button
        btn_refresh = Gtk.Button(label="Refresh Conflicts")
        btn_refresh.connect("clicked", self._load_conflicts)
        box.pack_start(btn_refresh, False, False, 0)

    def _init_settings_tab(self, box):
        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(20)
        grid.set_border_width(20)
        box.pack_start(grid, False, False, 0)
        
        # Autostart
        self.autostart_file = Path.home() / ".config/autostart/orchard.desktop"
        
        lbl_auto = Gtk.Label(label="<b>Startup</b>", use_markup=True, xalign=0)
        grid.attach(lbl_auto, 0, 0, 2, 1)
        
        self.check_autostart = Gtk.CheckButton(label="Start Orchard automatically on login")
        self.check_autostart.set_active(self.autostart_file.exists())
        self.check_autostart.connect("toggled", self._toggle_autostart)
        grid.attach(self.check_autostart, 0, 1, 2, 1)
        
        # Separator
        grid.attach(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), 0, 2, 2, 1)
        
        # Re-run Wizard
        lbl_wiz = Gtk.Label(label="<b>Account Management</b>", use_markup=True, xalign=0)
        grid.attach(lbl_wiz, 0, 3, 2, 1)
        
        lbl_wiz_desc = Gtk.Label(label="Need to change account or mount point?", xalign=0)
        grid.attach(lbl_wiz_desc, 0, 4, 1, 1)
        
        btn_wizard = Gtk.Button(label="Re-run Setup Wizard")
        btn_wizard.connect("clicked", self._open_wizard)
        grid.attach(btn_wizard, 1, 4, 1, 1)

    def _refresh_ui(self):
        # Update Status
        pending = self.engine.db.fetchone("SELECT COUNT(*) as c FROM actions WHERE status IN ('pending', 'processing')")
        count = pending['c'] if pending else 0
        self.lbl_pending.set_label(str(count))

        if not self.engine.drive_svc:
            self.lbl_state.set_markup("<span foreground='gray'>Offline</span>")
        elif count > 0:
            self.lbl_state.set_markup("<span foreground='#2196F3'>Syncing...</span>")
        else:
            self.lbl_state.set_markup("<span foreground='#4CAF50'>Everything is up to date</span>")
            
        return True

    # --- Actions ---

    def _toggle_autostart(self, btn):
        if btn.get_active():
            try:
                self.autostart_file.parent.mkdir(parents=True, exist_ok=True)
                repo_root = Path(__file__).parent.parent.parent.resolve()
                main_script = repo_root / "src/main.py"
                icon_path = repo_root / "src/assets/icons/orchard-logo.svg" # Absolute path for .desktop
                
                content = f"""[Desktop Entry]
Name=Orchard
Comment=iCloud Drive for Linux
Exec={sys.executable} {main_script}
Icon=orchard-logo
Terminal=false
Type=Application
Categories=Network;FileTransfer;
X-GNOME-Autostart-enabled=true
"""
                with open(self.autostart_file, 'w') as f:
                    f.write(content)
                os.chmod(self.autostart_file, 0o755)
            except Exception as e:
                print(f"Failed to enable autostart: {e}")
        else:
            if self.autostart_file.exists():
                try: self.autostart_file.unlink()
                except: pass

    def _open_wizard(self, _):
        from src.gui.wizard import run_wizard
        dialog = Gtk.MessageDialog(self, 0, Gtk.MessageType.WARNING, Gtk.ButtonsType.OK_CANCEL, "Restart Required")
        dialog.format_secondary_text("Running the setup wizard will require restarting Orchard to apply changes.")
        response = dialog.run()
        dialog.destroy()
        
        if response == Gtk.ResponseType.OK:
            self.hide()
            try:
                run_wizard()
                
                info = Gtk.MessageDialog(None, 0, Gtk.MessageType.INFO, Gtk.ButtonsType.OK, "Setup Complete")
                info.format_secondary_text("Please restart Orchard to apply changes.")
                info.run()
                info.destroy()
                
                Gtk.main_quit()
            except Exception as e:
                print(f"Wizard error: {e}")
                self.show_all()

    def _load_conflicts(self, _=None):
        for child in self.conflict_list.get_children():
            self.conflict_list.remove(child)
            
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
        self.engine.db.execute("UPDATE objects SET sync_state='pending_push', dirty=1 WHERE id=?", (obj_id,))
        self.engine.db.enqueue_action(obj_id, 'update_content', 'push', priority=20)
        self._load_conflicts()

    def _resolve_keep_cloud(self, btn, obj_id):
        self.engine.db.execute("UPDATE objects SET sync_state='pending_pull', dirty=0 WHERE id=?", (obj_id,))
        self.engine.db.execute("UPDATE drive_cache SET present_locally=0 WHERE object_id=?", (obj_id,))
        self.engine.db.enqueue_action(obj_id, 'ensure_latest', 'pull', priority=20)
        self._load_conflicts()

    def _on_about_dialog(self, _):
        from .about import OrchardAboutDialog
        about_dialog = OrchardAboutDialog(self)
        about_dialog.run()
        about_dialog.destroy()