import os
import subprocess
from urllib.parse import unquote
from gi.repository import Nautilus, GObject

class OrchardExtension(GObject.GObject, Nautilus.MenuProvider, Nautilus.InfoProvider, Nautilus.ColumnProvider):
    def __init__(self):
        print("OrchardExtension: Initialized")
        super().__init__()

    def get_columns(self):
        return (
            Nautilus.Column(
                name="OrchardExtension::status",
                attribute="orchard_status",
                label="Orchard Status",
                description="Sync status (Local/Cloud/Partial)",
            ),
        )

    def update_file_info(self, file):
        uri = file.get_uri()
        if not uri.startswith('file://'): return
        
        path = unquote(uri.replace('file://', ''))
        # print(f"OrchardExtension: Checking {path}") 
        
        try:
            # Read xattr (requires path to be accessible)
            # This might fail if path is not OrchardFS or other error
            status = os.getxattr(path, 'user.orchard.status').decode('utf-8')
            # print(f"OrchardExtension: Status for {path} is {status}")
            
            file.add_string_attribute('orchard_status', status.title())
            
            if status == 'local':
                file.add_emblem('emblem-orchard-local')
            elif status == 'cloud':
                file.add_emblem('emblem-orchard-cloud') 
            elif status == 'partial':
                file.add_emblem('emblem-orchard-partial')
            elif status == 'modified':
                file.add_emblem('emblem-orchard-modified')
            elif status == 'conflict':
                file.add_emblem('emblem-orchard-conflict')
                
        except Exception:
            # Not an orchard file or xattr not supported
            pass

    def get_file_items(self, *args):
        # Debugging signature mismatch
        # print(f"OrchardExtension: get_file_items called with {len(args)} args")
        
        window = None
        files = None

        if len(args) == 2:
            window, files = args
        elif len(args) == 1:
            # print(f"Arg 0 type: {type(args[0])}")
            if isinstance(args[0], list):
                files = args[0]
            else:
                # Assume it's files if it looks like a list
                files = args[0]
        else:
            return None

        if not files: return
        # print(f"OrchardExtension: Menu request for {len(files)} files")

        top_menuitem = Nautilus.MenuItem(
            name='OrchardMenu',
            label='Orchard',
            tip='Orchard Sync Actions',
            icon='orchard-logo'
        )

        submenu = Nautilus.Menu()
        top_menuitem.set_submenu(submenu)

        pin_item = Nautilus.MenuItem(
            name='OrchardPin',
            label='Make Available Offline',
            tip='Download and keep this file on disk',
            icon='drive-harddisk'
        )
        pin_item.connect('activate', self.pin_action, files)
        submenu.append_item(pin_item)

        unpin_item = Nautilus.MenuItem(
            name='OrchardUnpin',
            label='Free Up Space',
            tip='Remove local content to save space',
            icon='drive-cloud'
        )
        unpin_item.connect('activate', self.unpin_action, files)
        submenu.append_item(unpin_item)

        return [top_menuitem]

    def pin_action(self, menu, files):
        for file in files:
            path = unquote(file.get_uri().replace('file://', ''))
            subprocess.run(['setfattr', '-n', 'user.orchard.pinned', '-v', '1', path])

    def unpin_action(self, menu, files):
        for file in files:
            path = unquote(file.get_uri().replace('file://', ''))
            subprocess.run(['setfattr', '-n', 'user.orchard.pinned', '-v', '0', path])
