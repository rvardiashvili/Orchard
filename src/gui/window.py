import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk, Pango
import os
import sys
import subprocess
import threading
from pathlib import Path

# --- CSS Styling (System Theme Friendly) ---
CSS_DATA = b"""
.sidebar {
    border-right: 1px solid alpha(@borders, 0.5);
}
.sidebar-row {
    padding: 10px;
}
.card {
    /* Use theme background with slight transparency/darken for depth */
    background-color: alpha(@theme_bg_color, 0.5); 
    border: 1px solid alpha(@borders, 0.5);
    border-radius: 8px;
    padding: 15px;
    margin-bottom: 15px;
}
.status-header {
    font-size: 24px;
    font-weight: bold;
}
.brand-header {
    font-size: 28px;
    font-weight: bold;
    color: #FF3B30; /* Orchard Red */
}
.dim-label {
    opacity: 0.7;
}
.conflict-alert {
    background-color: @warning_bg_color;
    color: @warning_fg_color;
    border-radius: 6px;
    padding: 10px;
    border: 1px solid @warning_borders;
}
"""

class OrchardWindow(Gtk.Window):
    def __init__(self, engine, mount_point):
        super().__init__(title="Orchard")
        self.engine = engine
        self.mount_point = mount_point
        
        self.set_default_size(950, 700)
        self.set_position(Gtk.WindowPosition.CENTER)
        
        # 1. Register Custom Icons
        self._register_icons()
        
        # 2. Apply CSS
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(CSS_DATA)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        
        # Header Bar
        header = Gtk.HeaderBar(title="Orchard")
        header.set_show_close_button(True)
        header.props.subtitle = "iCloud for Linux"
        self.set_titlebar(header)
        
        btn_about = Gtk.Button.new_from_icon_name("help-about", Gtk.IconSize.MENU)
        btn_about.connect("clicked", self._on_about_dialog)
        header.pack_end(btn_about)
        
        # Window Icon
        try:
            icon_path = self.assets_path / "app/orchard-logo.svg"
            if icon_path.exists():
                self.set_icon_from_file(str(icon_path))
        except: pass

        # --- Main Layout (Sidebar + Stack) ---
        main_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.add(main_hbox)

        # Sidebar
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_UP_DOWN)
        
        self.sidebar = Gtk.StackSidebar()
        self.sidebar.set_stack(self.stack)
        self.sidebar.set_size_request(220, -1)
        self.sidebar.get_style_context().add_class("sidebar")
        
        main_hbox.pack_start(self.sidebar, False, False, 0)
        main_hbox.pack_start(self.stack, True, True, 0)

        # Pages
        self._init_overview_page()
        self._init_files_page()
        self._init_devices_page()
        self._init_account_page()
        self._init_settings_page()

        GLib.timeout_add_seconds(2, self._refresh_ui)

    def _register_icons(self):
        """Adds src/assets/icons to the GTK Icon Theme search path."""
        try:
            self.assets_path = Path(__file__).parent.parent.parent / "src/assets/icons"
            theme = Gtk.IconTheme.get_default()
            theme.append_search_path(str(self.assets_path))
            theme.append_search_path(str(self.assets_path / "app"))
            theme.append_search_path(str(self.assets_path / "categories"))
            theme.append_search_path(str(self.assets_path / "emblems"))
            theme.append_search_path(str(self.assets_path / "sidebar"))
            theme.append_search_path(str(self.assets_path / "actions"))
            theme.append_search_path(str(self.assets_path / "status"))
        except Exception as e:
            print(f"Failed to register icons: {e}")

    # =========================================================================
    # PAGE: OVERVIEW (Branded)
    # =========================================================================
    def _init_overview_page(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        vbox.set_border_width(30)
        scrolled.add(vbox)

        # -- BRANDING HEADER --
        brand_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        brand_box.set_halign(Gtk.Align.CENTER)
        brand_box.set_margin_bottom(10)
        
        logo_img = Gtk.Image.new_from_icon_name("orchard-logo", Gtk.IconSize.DIALOG)
        logo_img.set_pixel_size(64)
        brand_box.pack_start(logo_img, False, False, 0)
        
        lbl_brand = Gtk.Label(label="Orchard")
        lbl_brand.get_style_context().add_class("brand-header")
        brand_box.pack_start(lbl_brand, False, False, 0)
        
        vbox.pack_start(brand_box, False, False, 0)

        # -- CARD 1: Sync Status --
        status_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        status_card.get_style_context().add_class("card")
        
        # Initial icon
        self.img_status = Gtk.Image.new_from_icon_name("emblem-orchard-local", Gtk.IconSize.DIALOG)
        self.img_status.set_pixel_size(64)
        status_card.pack_start(self.img_status, False, False, 0)
        
        status_text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        status_text_box.set_valign(Gtk.Align.CENTER)
        
        self.lbl_status_title = Gtk.Label(label="Checking Status...", xalign=0)
        self.lbl_status_title.get_style_context().add_class("status-header")
        
        self.lbl_status_desc = Gtk.Label(label="Initializing...", xalign=0)
        self.lbl_status_desc.get_style_context().add_class("dim-label")
        
        status_text_box.pack_start(self.lbl_status_title, False, False, 0)
        status_text_box.pack_start(self.lbl_status_desc, False, False, 0)
        status_card.pack_start(status_text_box, True, True, 0)
        
        vbox.pack_start(status_card, False, False, 0)

        # -- CARD 2: Sync Controls --
        ctrl_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        ctrl_card.get_style_context().add_class("card")
        
        btn_open = Gtk.Button(label="Open Folder")
        btn_open.set_image(Gtk.Image.new_from_icon_name("orchard-action-open", Gtk.IconSize.BUTTON))
        btn_open.set_always_show_image(True)
        btn_open.connect("clicked", self._open_mount_point)
        ctrl_card.pack_start(btn_open, True, True, 0)
        
        self.btn_pause = Gtk.ToggleButton(label="Pause")
        self.btn_pause.set_image(Gtk.Image.new_from_icon_name("orchard-action-pause", Gtk.IconSize.BUTTON))
        self.btn_pause.set_always_show_image(True)
        self.btn_pause.connect("toggled", self._on_pause_toggled)
        ctrl_card.pack_start(self.btn_pause, True, True, 0)
        
        btn_refresh = Gtk.Button(label="Force Sync")
        btn_refresh.set_image(Gtk.Image.new_from_icon_name("orchard-action-refresh", Gtk.IconSize.BUTTON))
        btn_refresh.set_always_show_image(True)
        btn_refresh.connect("clicked", self._force_sync)
        ctrl_card.pack_start(btn_refresh, True, True, 0)
        
        vbox.pack_start(ctrl_card, False, False, 0)

        # -- CARD 3: Storage --
        storage_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        storage_card.get_style_context().add_class("card")
        
        st_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        st_head.pack_start(Gtk.Label(label="<b>iCloud Storage</b>", use_markup=True, xalign=0), True, True, 0)
        btn_ref = Gtk.Button.new_from_icon_name("orchard-action-refresh", Gtk.IconSize.BUTTON)
        btn_ref.connect("clicked", self._force_refresh_quota)
        st_head.pack_start(btn_ref, False, False, 0)
        storage_card.pack_start(st_head, False, False, 0)
        
        self.storage_bar = Gtk.ProgressBar()
        self.storage_bar.set_show_text(True)
        storage_card.pack_start(self.storage_bar, False, False, 0)
        self.lbl_storage_total = Gtk.Label(label="", xalign=0.5)
        self.lbl_storage_total.get_style_context().add_class("dim-label")
        storage_card.pack_start(self.lbl_storage_total, False, False, 0)
        
        self.quota_list = Gtk.ListBox()
        self.quota_list.set_selection_mode(Gtk.SelectionMode.NONE)
        storage_card.pack_start(self.quota_list, False, False, 0)
        
        vbox.pack_start(storage_card, False, False, 0)

        # -- CARD 4: Activity --
        activity_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        activity_card.get_style_context().add_class("card")
        activity_card.pack_start(Gtk.Label(label="<b>Recent Activity</b>", use_markup=True, xalign=0), False, False, 0)
        
        self.activity_list_box = Gtk.ListBox()
        self.activity_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        activity_card.pack_start(self.activity_list_box, False, False, 0)
        
        vbox.pack_start(activity_card, True, True, 0)

        self.stack.add_titled(scrolled, "overview", "Overview")
        self.stack.child_set_property(scrolled, "icon-name", "orchard-tab-overview")

    # =========================================================================
    # OTHER PAGES
    # =========================================================================
    def _init_files_page(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        vbox.set_border_width(30)
        scrolled.add(vbox)

        # Conflicts
        self.conflict_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.conflict_frame.get_style_context().add_class("conflict-alert")
        self.conflict_frame.set_no_show_all(True); self.conflict_frame.set_visible(False)
        
        c_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        c_head.pack_start(Gtk.Image.new_from_icon_name("orchard-logo-conflict", Gtk.IconSize.MENU), False, False, 0)
        c_head.pack_start(Gtk.Label(label="<b>Conflicts Detected</b>", use_markup=True), False, False, 0)
        self.conflict_frame.pack_start(c_head, False, False, 0)
        
        self.conflict_list = Gtk.ListBox()
        self.conflict_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.conflict_frame.pack_start(self.conflict_list, True, True, 0)
        vbox.pack_start(self.conflict_frame, False, False, 0)

        # Local Cache
        cache_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        cache_card.get_style_context().add_class("card")
        
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        top.pack_start(Gtk.Label(label="<b>Local Cache</b>", use_markup=True, xalign=0), True, True, 0)
        btn_ref = Gtk.Button.new_from_icon_name("orchard-action-refresh", Gtk.IconSize.BUTTON)
        btn_ref.connect("clicked", self._load_storage)
        top.pack_start(btn_ref, False, False, 0)
        cache_card.pack_start(top, False, False, 0)
        
        self.cache_list = Gtk.ListBox()
        self.cache_list.set_selection_mode(Gtk.SelectionMode.NONE)
        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_min_content_height(300)
        list_scroll.add(self.cache_list)
        cache_card.pack_start(list_scroll, True, True, 0)
        
        act = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        act.set_halign(Gtk.Align.END)
        btn_purge = Gtk.Button(label="Evict Unpinned")
        btn_purge.set_image(Gtk.Image.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.BUTTON))
        btn_purge.set_always_show_image(True)
        # btn_purge.get_style_context().add_class("destructive-action") # Optional
        btn_purge.connect("clicked", self._purge_cache)
        act.pack_start(btn_purge, False, False, 0)
        cache_card.pack_start(act, False, False, 0)

        vbox.pack_start(cache_card, True, True, 0)
        self.stack.add_titled(scrolled, "files", "Files")
        self.stack.child_set_property(scrolled, "icon-name", "orchard-tab-files")

    def _init_devices_page(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        vbox.set_border_width(30)
        
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.pack_start(Gtk.Label(label="<span size='large'><b>My Devices</b></span>", use_markup=True), True, True, 0)
        btn_refresh = Gtk.Button.new_from_icon_name("orchard-action-refresh", Gtk.IconSize.BUTTON)
        btn_refresh.connect("clicked", self._load_devices)
        header.pack_start(btn_refresh, False, False, 0)
        vbox.pack_start(header, False, False, 0)
        
        scrolled = Gtk.ScrolledWindow()
        self.devices_list = Gtk.ListBox()
        self.devices_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled.add(self.devices_list)
        
        frame = Gtk.Frame()
        frame.get_style_context().add_class("card")
        frame.add(scrolled)
        vbox.pack_start(frame, True, True, 0)
        
        GLib.timeout_add(1000, self._load_devices)
        self.stack.add_titled(vbox, "devices", "Devices")
        self.stack.child_set_property(vbox, "icon-name", "orchard-tab-devices")

    def _init_account_page(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        vbox.set_border_width(30)
        vbox.set_valign(Gtk.Align.CENTER)
        
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        card.get_style_context().add_class("card")
        card.set_halign(Gtk.Align.CENTER)
        card.set_size_request(400, -1)
        
        icon = Gtk.Image.new_from_icon_name("avatar-default", Gtk.IconSize.DIALOG)
        icon.set_pixel_size(96)
        card.pack_start(icon, False, False, 10)
        
        self.lbl_acc_name = Gtk.Label()
        self.lbl_acc_name.set_markup("<span size='xx-large' weight='bold'>Loading...</span>")
        card.pack_start(self.lbl_acc_name, False, False, 0)
        
        self.lbl_acc_email = Gtk.Label(label="")
        self.lbl_acc_email.get_style_context().add_class("dim-label")
        card.pack_start(self.lbl_acc_email, False, False, 0)
        
        grid = Gtk.Grid()
        grid.set_column_spacing(20)
        grid.set_row_spacing(10)
        grid.set_halign(Gtk.Align.CENTER)
        
        def _add_row(idx, t, w):
            grid.attach(Gtk.Label(label=f"<b>{t}:</b>", use_markup=True, xalign=1), 0, idx, 1, 1)
            grid.attach(w, 1, idx, 1, 1)
            
        self.lbl_acc_security = Gtk.Label(label="...", xalign=0)
        _add_row(0, "Security", self.lbl_acc_security)
        self.lbl_acc_locale = Gtk.Label(label="...", xalign=0)
        _add_row(1, "Region", self.lbl_acc_locale)
        self.lbl_acc_managed = Gtk.Label(label="...", xalign=0)
        _add_row(2, "Managed", self.lbl_acc_managed)
        
        card.pack_start(grid, False, False, 10)
        
        btn_logout = Gtk.Button(label="Sign Out & Reset")
        btn_logout.get_style_context().add_class("destructive-action")
        btn_logout.connect("clicked", self._on_logout)
        card.pack_start(btn_logout, False, False, 10)
        
        vbox.pack_start(card, False, False, 0)
        
        GLib.timeout_add(500, self._load_account_info)
        self.stack.add_titled(vbox, "account", "Account")
        self.stack.child_set_property(vbox, "icon-name", "orchard-tab-account")

    def _init_settings_page(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        vbox.set_border_width(30)
        scrolled.add(vbox)
        
        # General
        gen_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        gen_card.get_style_context().add_class("card")
        gen_card.pack_start(Gtk.Label(label="<b>General</b>", use_markup=True, xalign=0), False, False, 0)
        
        self.autostart_file = Path.home() / ".config/autostart/orchard.desktop"
        self.check_autostart = Gtk.CheckButton(label="Start Orchard automatically on login")
        self.check_autostart.set_active(self.autostart_file.exists())
        self.check_autostart.connect("toggled", self._toggle_autostart)
        gen_card.pack_start(self.check_autostart, False, False, 0)
        
        btn_wiz = Gtk.Button(label="Re-run Setup Wizard")
        btn_wiz.set_halign(Gtk.Align.START)
        btn_wiz.connect("clicked", self._open_wizard)
        gen_card.pack_start(btn_wiz, False, False, 0)
        
        vbox.pack_start(gen_card, False, False, 0)
        
        # Logs
        log_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        log_card.get_style_context().add_class("card")
        
        log_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        log_head.pack_start(Gtk.Label(label="<b>System Logs</b>", use_markup=True, xalign=0), True, True, 0)
        
        btn_refresh_log = Gtk.Button.new_from_icon_name("orchard-action-refresh", Gtk.IconSize.BUTTON)
        btn_refresh_log.connect("clicked", self._load_logs)
        log_head.pack_start(btn_refresh_log, False, False, 0)
        log_card.pack_start(log_head, False, False, 0)
        
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD)
        
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_min_content_height(200)
        log_scroll.add(self.log_view)
        log_card.pack_start(log_scroll, True, True, 0)
        
        vbox.pack_start(log_card, True, True, 0)
        
        GLib.timeout_add(1000, self._load_logs)
        self.stack.add_titled(scrolled, "settings", "Settings")
        self.stack.child_set_property(scrolled, "icon-name", "orchard-tab-settings")

    # =========================================================================
    # REFRESH UI
    # =========================================================================
    def _refresh_ui(self):
        pending = self.engine.db.fetchone("SELECT COUNT(*) as c FROM actions WHERE status IN ('pending', 'processing')")
        count = pending['c'] if pending else 0
        
        # Check authentication state
        if not self.engine.connected:
            self.lbl_status_title.set_text("Offline")
            self.lbl_status_desc.set_text("No internet connection")
            self.img_status.set_from_icon_name("orchard-status-offline", Gtk.IconSize.DIALOG)
        elif self.engine.paused:
            self.lbl_status_title.set_text("Paused")
            self.lbl_status_desc.set_text("Synchronization is suspended")
            self.img_status.set_from_icon_name("orchard-action-pause", Gtk.IconSize.DIALOG)
        elif not self.engine.api.authenticated:
            self.lbl_status_title.set_text("Authentication Required")
            self.lbl_status_desc.set_text("Please sign in or verify session")
            self.img_status.set_from_icon_name("orchard-status-error", Gtk.IconSize.DIALOG)
        elif count > 0:
            self.lbl_status_title.set_text(f"Syncing {count} items")
            self.lbl_status_desc.set_text("Changes are being synchronized")
            # We don't have a custom spinner, use standard
            self.img_status.set_from_icon_name("emblem-synchronizing-symbolic", Gtk.IconSize.DIALOG)
        else:
            self.lbl_status_title.set_text("Up to Date")
            self.lbl_status_desc.set_text("Files are in sync with iCloud")
            self.img_status.set_from_icon_name("emblem-orchard-local", Gtk.IconSize.DIALOG)

        self._load_activity()
        self._check_conflicts()
        
        page = self.stack.get_visible_child_name()
        if page == "overview": self._update_quota_ui()
        elif page == "files": self._load_storage()
        return True

    def _on_pause_toggled(self, btn):
        self.engine.paused = btn.get_active()
        btn.set_label("Resume" if self.engine.paused else "Pause")
        icon = "orchard-action-resume" if self.engine.paused else "orchard-action-pause"
        btn.set_image(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON))

    def _open_mount_point(self, _):
        subprocess.Popen(["xdg-open", self.mount_point])

    def _force_sync(self, _):
        self.engine.executor.submit(self.engine._pull_metadata)

    def _on_logout(self, _):
        dialog = Gtk.MessageDialog(self, 0, Gtk.MessageType.WARNING, Gtk.ButtonsType.OK_CANCEL, "Sign Out")
        dialog.format_secondary_text("This will clear your local session and restart Orchard. You will need to sign in again.")
        if dialog.run() == Gtk.ResponseType.OK:
            self.engine.api.logout()
            self._open_wizard(None)
        dialog.destroy()

    def _load_activity(self):
        for child in self.activity_list_box.get_children(): self.activity_list_box.remove(child)
        rows = self.engine.db.fetchall("""
            SELECT a.action_type, a.direction, a.status, o.name 
            FROM actions a LEFT JOIN objects o ON a.target_id = o.id 
            WHERE a.status IN ('processing', 'pending', 'completed') 
            ORDER BY a.created_at DESC LIMIT 5
        """)
        if not rows: self.activity_list_box.add(Gtk.Label(label="No recent activity.", xalign=0, margin=10))
        for row in rows:
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            box.set_margin_top(5); box.set_margin_bottom(5)
            
            icon = "emblem-synchronizing-symbolic"
            if row['status'] == 'completed': icon = "emblem-ok-symbolic"
            
            box.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU), False, False, 0)
            d = f"{row['action_type'].title()} '{row['name'] or 'Unknown'}'"
            box.pack_start(Gtk.Label(label=d, xalign=0), True, True, 0)
            self.activity_list_box.add(box)
        self.activity_list_box.show_all()

    def _update_quota_ui(self):
        if not self.engine.storage_info: return
        info = self.engine.storage_info.get('storageUsageInfo', self.engine.storage_info)
        total = info.get('totalStorageInBytes', 0)
        used = info.get('usedStorageInBytes', info.get('compStorageInBytes', 0))
        
        def fmt(b):
            for u in ['B', 'KB', 'MB', 'GB', 'TB']:
                if b < 1024: return f"{b:.1f} {u}"
                b /= 1024
            return f"{b:.1f} PB"

        if total > 0:
            fraction = min(1.0, used / total)
            self.storage_bar.set_fraction(fraction)
            self.storage_bar.set_text(f"{fraction*100:.1f}%")
            self.lbl_storage_total.set_text(f"{fmt(used)} of {fmt(total)} used")

        # Breakdown
        for c in self.quota_list.get_children(): self.quota_list.remove(c)
        medias = self.engine.storage_info.get('storageUsageByMedia', [])
        max_val = max([m.get('usageInBytes', 0) for m in medias]) if medias else 1
        
        for m in medias:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.set_border_width(5)
            
            key = m.get('mediaKey')
            icon_name = "orchard-docs" # fallback
            if key == 'photos': icon_name = "orchard-photos"
            elif key == 'backup': icon_name = "orchard-backup"
            elif key == 'mail': icon_name = "orchard-mail"
            elif key == 'messages': icon_name = "orchard-messages"
            
            try:
                img = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
                row.pack_start(img, False, False, 0)
            except: pass
            
            row.pack_start(Gtk.Label(label=m.get('displayLabel', key), xalign=0), False, False, 0)
            
            bar = Gtk.ProgressBar()
            bar.set_fraction(m.get('usageInBytes', 0) / max_val)
            bar.set_valign(Gtk.Align.CENTER)
            row.pack_start(bar, True, True, 0)
            
            row.pack_start(Gtk.Label(label=fmt(m.get('usageInBytes', 0))), False, False, 0)
            self.quota_list.add(row)
        self.quota_list.show_all()

    def _force_refresh_quota(self, _):
        self.engine.executor.submit(self.engine.fetch_storage_usage)

    # --- Files Logic ---
    def _check_conflicts(self):
        rows = self.engine.db.fetchall("SELECT * FROM objects WHERE sync_state='conflict'")
        if rows:
            self.conflict_frame.set_visible(True); self.conflict_frame.show_all()
            for c in self.conflict_list.get_children(): self.conflict_list.remove(c)
            for row in rows:
                b = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                b.pack_start(Gtk.Label(label=row['name']), True, True, 0)
                btn1 = Gtk.Button(label="Keep Local")
                btn1.connect("clicked", self._resolve_keep_local, row['id'])
                b.pack_start(btn1, False, False, 0)
                btn2 = Gtk.Button(label="Keep Cloud")
                btn2.connect("clicked", self._resolve_keep_cloud, row['id'])
                b.pack_start(btn2, False, False, 0)
                self.conflict_list.add(b)
            self.conflict_list.show_all()
        else: self.conflict_frame.set_visible(False)

    def _resolve_keep_local(self, btn, obj_id):
        self.engine.db.execute("UPDATE objects SET sync_state='pending_push', dirty=1 WHERE id=?", (obj_id,))
        self.engine.db.enqueue_action(obj_id, 'update_content', 'push', priority=20)
        self._check_conflicts()

    def _resolve_keep_cloud(self, btn, obj_id):
        self.engine.db.execute("UPDATE objects SET sync_state='pending_pull', dirty=0 WHERE id=?", (obj_id,))
        self.engine.db.execute("UPDATE drive_cache SET present_locally=0 WHERE object_id=?", (obj_id,))
        self.engine.db.enqueue_action(obj_id, 'ensure_latest', 'pull', priority=20)
        self._check_conflicts()

    def _load_storage(self, _=None):
        for c in self.cache_list.get_children(): self.cache_list.remove(c)
        rows = self.engine.db.fetchall("""
            SELECT o.id, o.name, dc.size, dc.pinned, dc.present_locally 
            FROM drive_cache dc JOIN objects o ON dc.object_id = o.id 
            WHERE dc.present_locally > 0 ORDER BY dc.size DESC LIMIT 50
        """)
        if not rows: self.cache_list.add(Gtk.Label(label="Cache is empty."))
        for r in rows:
            b = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            b.set_margin_top(5); b.set_margin_bottom(5)
            icon = "emblem-orchard-local"
            if r['pinned']: icon = "view-pin-symbolic" # Or custom if you make one
            elif r['present_locally'] == 2: icon = "emblem-orchard-partial"
            
            try: b.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU), False, False, 0)
            except: pass
            
            sz = (r['size'] or 0) / (1024*1024)
            b.pack_start(Gtk.Label(label=f"{r['name']} ({sz:.1f} MB)", xalign=0), True, True, 0)
            
            if not r['pinned']:
                btn = Gtk.Button.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.BUTTON)
                btn.connect("clicked", self._evict_file, r['id'])
                b.pack_start(btn, False, False, 0)
            self.cache_list.add(b)
        self.cache_list.show_all()

    def _evict_file(self, btn, obj_id):
        self.engine.db.execute("UPDATE drive_cache SET pinned=0, present_locally=0 WHERE object_id=?", (obj_id,))
        r = self.engine.db.fetchone("SELECT local_path FROM drive_cache WHERE object_id=?", (obj_id,))
        if r and r['local_path'] and os.path.exists(r['local_path']):
             with open(r['local_path'], 'wb') as f: pass
        self._load_storage()

    def _purge_cache(self, _):
        self.engine.db.execute("UPDATE drive_cache SET present_locally=0 WHERE pinned=0")
        rows = self.engine.db.fetchall("SELECT local_path FROM drive_cache WHERE pinned=0")
        for r in rows:
            if r['local_path'] and os.path.exists(r['local_path']):
                with open(r['local_path'], 'wb') as f: pass
        self._load_storage()

    # --- Devices ---
    def _load_devices(self, _=None):
        def _f():
            try:
                d = self.engine.api.get_devices()
                GLib.idle_add(self._update_devices_ui, d)
            except Exception as e:
                GLib.idle_add(self._show_device_error, str(e))
        self.engine.executor.submit(_f)

    def _update_devices_ui(self, devices):
        for c in self.devices_list.get_children(): self.devices_list.remove(c)
        if not devices: self._show_device_placeholder(); return
        for d in devices:
            r = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
            r.set_margin_top(10); r.set_margin_bottom(10)
            
            icon = "phone"
            cls = str(d.get('deviceClass', '')).lower()
            if 'ipad' in cls: icon = "tablet"
            elif 'mac' in cls: icon = "computer"
            
            if d.get('source') == 'capabilities_fallback':
                if 'iphone' in cls: icon = "phone"
                elif 'mac' in cls: icon = "computer"
            
            r.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.DIALOG), False, False, 0)
            
            info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            name = d.get('name', 'Unknown')
            model = d.get('modelDisplayName', 'Apple Device')
            
            info.pack_start(Gtk.Label(label=f"<b>{name}</b>", use_markup=True, xalign=0), False, False, 0)
            info.pack_start(Gtk.Label(label=model, xalign=0, properties={"attributes": Pango.AttrList.from_string("foreground='#888'")}), False, False, 0)
            
            batt = d.get('batteryLevel')
            if batt is not None:
                info.pack_start(Gtk.Label(label=f"Battery: {int(batt)}%", xalign=0), False, False, 0)
            r.pack_start(info, True, True, 0)
            self.devices_list.add(r)
        self.devices_list.show_all()

    def _show_device_error(self, msg):
        self.devices_list.add(Gtk.Label(label=f"Error: {msg}"))
        self.devices_list.show_all()

    def _show_device_placeholder(self):
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        b.set_valign(Gtk.Align.CENTER)
        b.pack_start(Gtk.Label(label="Devices Unavailable", xalign=0.5), False, False, 0)
        btn = Gtk.Button(label="Verify Session")
        btn.connect("clicked", self._verify_session)
        b.pack_start(btn, False, False, 0)
        self.devices_list.add(b)
        self.devices_list.show_all()

    def _verify_session(self, btn):
        btn.set_sensitive(False); btn.set_label("Verifying...")
        def _bg():
            self.engine.api.authenticate(input_callback=self._auth_input_callback)
            GLib.idle_add(self._load_devices)
        threading.Thread(target=_bg, daemon=True).start()

    def _auth_input_callback(self, type, msg, options=None):
        res = {'val': None}; ev = threading.Event()
        def _prompt():
            d = Gtk.MessageDialog(self, 0, Gtk.MessageType.QUESTION, Gtk.ButtonsType.OK_CANCEL, "Auth")
            d.format_secondary_text(msg)
            e = Gtk.Entry()
            if type=='password': e.set_visibility(False)
            d.get_content_area().pack_start(e, True, True, 10); d.show_all()
            if d.run() == Gtk.ResponseType.OK: res['val'] = e.get_text()
            d.destroy(); ev.set()
        GLib.idle_add(_prompt); ev.wait(); return res['val']

    # --- Account & Log ---
    def _load_account_info(self):
        def _f():
            try:
                i = self.engine.api.get_account_info()
                GLib.idle_add(self._update_account_ui, i)
            except: pass
        self.engine.executor.submit(_f)

    def _update_account_ui(self, i):
        if not i: return
        self.lbl_acc_name.set_markup(f"<span size='xx-large' weight='bold'>{i.get('full_name')}</span>")
        self.lbl_acc_email.set_text(i.get('apple_id', ''))
        sec = "Standard"
        if i.get('hsa_version', 0) >= 2: sec = "2FA Enabled"
        self.lbl_acc_security.set_text(sec)
        self.lbl_acc_locale.set_text(i.get('locale', ''))
        self.lbl_acc_managed.set_text("Yes" if i.get('is_managed') else "No")

    def _load_logs(self, _=None):
        lp = Path.home() / ".cache/orchard/orchard.log"
        if lp.exists():
            with open(lp, 'r') as f: self.log_view.get_buffer().set_text("".join(f.readlines()[-100:]))

    def _toggle_autostart(self, btn):
        if btn.get_active(): pass # Add logic
        else:
            if self.autostart_file.exists(): 
                try: self.autostart_file.unlink()
                except: pass

    def _open_wizard(self, _):
        subprocess.Popen([sys.executable, "-c", "import sys, subprocess; sys.path.insert(0, '.'); from src.gui.wizard import run_wizard; run_wizard(); subprocess.Popen([sys.executable, 'src/main.py'])"], cwd=str(Path(__file__).parent.parent.parent))
        Gtk.main_quit()

    def _on_about_dialog(self, _):
        try:
            from src.gui.about import OrchardAboutDialog
            about = OrchardAboutDialog(self)
            about.run()
            about.destroy()
        except ImportError:
            about = Gtk.AboutDialog()
            about.set_program_name("Orchard")
            about.set_version("1.0.0")
            about.set_copyright("© 2024 Rati Vardiashvili")
            about.set_comments("iCloud Drive Client for Linux")
            about.set_website("https://github.com/rvardiashvili/orchard")
            about.set_logo_icon_name("orchard-logo")
            about.run()
            about.destroy()
