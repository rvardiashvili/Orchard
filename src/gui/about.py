import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GdkPixbuf
from pathlib import Path

class OrchardAboutDialog(Gtk.AboutDialog):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True)
        self.set_program_name("Orchard")
        self.set_version("0.1.0-alpha")
        self.set_comments("iCloud for Linux - Seamlessly integrates your Apple ecosystem.")
        self.set_copyright("© 2026 Rati Vardiashvili")
        self.set_license_type(Gtk.License.MIT_X11) # Or Gtk.License.MIT_X11 
        self.set_website("https://github.com/rvardiashvili/Orchard") 
        self.set_website_label("Orchard GitHub Repository")
        self.set_authors(["Rati Vardiashvili"])
        self.set_artists(["Rati Vardiashvili"])
        
        # Set custom logo
        try:
            icon_path = Path(__file__).parent.parent.parent / "src/assets/icons/orchard-logo.svg"
            if icon_path.exists():
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon_path), 128, 128, True)
                self.set_logo(pixbuf)
        except Exception as e:
            print(f"Error loading about dialog logo: {e}")
