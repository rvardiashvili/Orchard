import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf
import threading
import sys
import os
from pathlib import Path

from src.config.manager import ConfigManager
from src.icloud_client.client import OrchardiCloudClient

class OrchardWizard(Gtk.Assistant):
    def __init__(self):
        super().__init__()
        self.set_title("Orchard Setup")
        self.set_default_size(600, 450)
        self.set_position(Gtk.WindowPosition.CENTER)
        
        self.config = ConfigManager()
        self.client = None
        
        self.connect("cancel", self._on_cancel)
        self.connect("close", self._on_cancel)
        self.connect("apply", self._on_apply)
        self.connect("prepare", self._on_prepare)

        # Window Icon
        try:
            icon_path = Path(__file__).parent.parent.parent / "src/assets/icons/orchard-logo.svg"
            if icon_path.exists():
                self.set_icon_from_file(str(icon_path))
        except: pass

        self._init_pages()
        
    def _init_pages(self):
        # PAGE 1: Welcome
        self.page_welcome = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.page_welcome.set_border_width(40)
        self.page_welcome.set_valign(Gtk.Align.CENTER)
        
        # Logo (Scaled)
        try:
            # Resolve absolute path to assets
            base_path = Path(__file__).parent.parent.parent
            logo_path = base_path / "src/assets/icons/orchard-logo.svg"
            
            if logo_path.exists():
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(logo_path), 128, 128, True)
                img = Gtk.Image.new_from_pixbuf(pixbuf)
                self.page_welcome.pack_start(img, True, True, 0)
        except Exception as e:
            print(f"Failed to load logo: {e}")

        lbl = Gtk.Label()
        lbl.set_markup("<span size='xx-large' weight='bold'>Welcome to Orchard</span>")
        self.page_welcome.pack_start(lbl, True, True, 0)
        
        lbl = Gtk.Label()
        lbl.set_markup("<span size='xx-large' weight='bold'>Welcome to Orchard</span>")
        self.page_welcome.pack_start(lbl, True, True, 0)
        
        desc = Gtk.Label(label="iCloud for Linux.\nSync your files seamlessly.")
        desc.set_justify(Gtk.Justification.CENTER)
        self.page_welcome.pack_start(desc, True, True, 0)
        desc.set_justify(Gtk.Justification.CENTER)
        self.page_welcome.pack_start(desc, True, True, 0)
        
        self.append_page(self.page_welcome)
        self.set_page_title(self.page_welcome, "Welcome")
        self.set_page_type(self.page_welcome, Gtk.AssistantPageType.INTRO)
        self.set_page_complete(self.page_welcome, True)
        
        # PAGE 2: Configuration
        self.page_config = Gtk.Grid()
        self.page_config.set_row_spacing(15)
        self.page_config.set_column_spacing(15)
        self.page_config.set_border_width(40)
        self.page_config.set_valign(Gtk.Align.CENTER)
        self.page_config.set_halign(Gtk.Align.CENTER)
        
        self.entry_apple_id = Gtk.Entry()
        self.entry_apple_id.set_placeholder_text("appleid@example.com")
        self.entry_apple_id.set_width_chars(30)
        if self.config.apple_id: self.entry_apple_id.set_text(self.config.apple_id)
        
        self.entry_mount = Gtk.FileChooserButton(title="Select Mount Point", action=Gtk.FileChooserAction.SELECT_FOLDER)
        self.entry_mount.set_width_chars(30)
        if self.config.mount_point: self.entry_mount.set_current_folder(self.config.mount_point)
        
        self.check_autostart = Gtk.CheckButton(label="Start Orchard automatically on login")
        self.check_autostart.set_active(True)

        # Signals
        self.entry_apple_id.connect("changed", self._validate_config)
        self.entry_mount.connect("file-set", self._validate_config)
        
        self.page_config.attach(Gtk.Label(label="Apple ID:"), 0, 0, 1, 1)
        self.page_config.attach(self.entry_apple_id, 1, 0, 1, 1)
        self.page_config.attach(Gtk.Label(label="Mount Point:"), 0, 1, 1, 1)
        self.page_config.attach(self.entry_mount, 1, 1, 1, 1)
        self.page_config.attach(self.check_autostart, 1, 2, 1, 1)
        
        self.append_page(self.page_config)
        self.set_page_title(self.page_config, "Configuration")
        self.set_page_type(self.page_config, Gtk.AssistantPageType.CONTENT)
        
        # PAGE 3: Authentication (Progress)
        self.page_auth = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.page_auth.set_border_width(20)
        self.lbl_auth_status = Gtk.Label(label="Ready to authenticate...")
        self.spinner = Gtk.Spinner()
        self.page_auth.pack_start(self.lbl_auth_status, True, True, 0)
        self.page_auth.pack_start(self.spinner, True, True, 0)
        
        self.append_page(self.page_auth)
        self.set_page_title(self.page_auth, "Authentication")
        self.set_page_type(self.page_auth, Gtk.AssistantPageType.PROGRESS)
        
        # PAGE 4: Done
        self.page_done = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.page_done.set_border_width(20)
        self.page_done.pack_start(Gtk.Label(label="Setup Complete!"), True, True, 0)
        
        self.append_page(self.page_done)
        self.set_page_title(self.page_done, "Finish")
        self.set_page_type(self.page_done, Gtk.AssistantPageType.SUMMARY)

    def _validate_config(self, *args):
        apple_id = self.entry_apple_id.get_text()
        mount = self.entry_mount.get_filename()
        is_complete = bool(apple_id and mount)
        self.set_page_complete(self.page_config, is_complete)

    def _on_prepare(self, assistant, page):
        # Validate Config Page
        if page == self.page_config:
            self._validate_config()
            
        # Start Auth on Auth Page
        if page == self.page_auth:
            self._start_authentication()

    def _start_authentication(self):
        apple_id = self.entry_apple_id.get_text()
        mount_point = self.entry_mount.get_filename()
        
        if not apple_id or not mount_point:
            self.lbl_auth_status.set_label("Error: Missing fields")
            return

        self.config.set("apple_id", apple_id)
        self.config.set("mount_point", mount_point)
        
        self.lbl_auth_status.set_label(f"Authenticating as {apple_id}...")
        self.spinner.start()
        
        # Run auth in background thread so GUI doesn't freeze
        # But prompts must run on main thread via GLib.idle_add? 
        # No, input_callback runs in the client thread. It must invoke GUI on Main Thread.
        
        import threading
        t = threading.Thread(target=self._auth_thread)
        t.daemon = True
        t.start()

    def _auth_thread(self):
        self.client = OrchardiCloudClient(
            self.config.apple_id,
            cookie_directory=self.config.cookie_dir
        )
        
        try:
            self.client.authenticate(input_callback=self._gui_input_callback)
            
            if self.client.authenticated:
                GLib.idle_add(self._auth_success)
            else:
                GLib.idle_add(self._auth_failed, "Authentication failed.")
        except Exception as e:
            GLib.idle_add(self._auth_failed, str(e))

    def _gui_input_callback(self, prompt_type, message, options=None):
        # This runs in background thread. Must invoke dialog on main thread and wait.
        # We use a Condition variable or queue to wait for main thread result.
        
        result_holder = {"value": None}
        condition = threading.Condition()
        
        def show_dialog():
            dialog = None
            if prompt_type == "password":
                dialog = Gtk.MessageDialog(self, 0, Gtk.MessageType.QUESTION, Gtk.ButtonsType.OK_CANCEL, "Password Required")
                dialog.format_secondary_text(message)
                entry = Gtk.Entry()
                entry.set_visibility(False)
                entry.connect("activate", lambda w: dialog.response(Gtk.ResponseType.OK))
                dialog.get_content_area().add(entry)
                dialog.show_all()
                
                resp = dialog.run()
                if resp == Gtk.ResponseType.OK:
                    result_holder["value"] = entry.get_text()
                dialog.destroy()
                
            elif prompt_type == "2fa_code":
                dialog = Gtk.MessageDialog(self, 0, Gtk.MessageType.QUESTION, Gtk.ButtonsType.OK_CANCEL, "Verification Code")
                dialog.format_secondary_text(message)
                entry = Gtk.Entry()
                entry.connect("activate", lambda w: dialog.response(Gtk.ResponseType.OK))
                dialog.get_content_area().add(entry)
                dialog.show_all()
                
                resp = dialog.run()
                if resp == Gtk.ResponseType.OK:
                    result_holder["value"] = entry.get_text()
                dialog.destroy()
                
            elif prompt_type == "device_select":
                # Not implemented nicely yet, defaulting to 0
                result_holder["value"] = "0"

            with condition:
                condition.notify()
                
        GLib.idle_add(show_dialog)
        
        with condition:
            condition.wait()
            
        return result_holder["value"] or ""

    def _auth_success(self):
        self.spinner.stop()
        self.lbl_auth_status.set_label("Authentication Successful!")
        self.set_page_complete(self.page_auth, True)
        self.commit() # Jump to next page automatically? No, user clicks Next.

    def _auth_failed(self, error):
        self.spinner.stop()
        self.lbl_auth_status.set_label(f"Error: {error}")
        # Allow retry?
        
    def _on_apply(self, assistant):
        # Configure Autostart
        autostart_dir = Path.home() / ".config/autostart"
        autostart_file = autostart_dir / "orchard.desktop"
        
        if self.check_autostart.get_active():
            autostart_dir.mkdir(parents=True, exist_ok=True)
            
            # Resolve paths
            repo_root = Path(__file__).parent.parent.parent.resolve()
            main_script = repo_root / "src/main.py"
            icon_name = "orchard-logo"
            
            content = f"""[Desktop Entry]
Name=Orchard
Comment=iCloud Drive for Linux
Exec={sys.executable} {main_script}
Icon={icon_name}
Terminal=false
Type=Application
Categories=Network;FileTransfer;
X-GNOME-Autostart-enabled=true
"""
            try:
                with open(autostart_file, 'w') as f:
                    f.write(content)
                os.chmod(autostart_file, 0o755)
            except Exception as e:
                print(f"Failed to enable autostart: {e}")
        else:
            if autostart_file.exists():
                try: autostart_file.unlink()
                except: pass

        Gtk.main_quit()

    def _on_cancel(self, assistant):
        Gtk.main_quit()
        import sys
        sys.exit(0) # Exit app if setup cancelled

def run_wizard():
    wizard = OrchardWizard()
    wizard.show_all()
    Gtk.main()
