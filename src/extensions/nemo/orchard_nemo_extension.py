import os
import subprocess
from urllib.parse import unquote
from gi.repository import Nemo, GObject

class OrchardNemoExtension(GObject.GObject, Nemo.MenuProvider, Nemo.InfoProvider):
    def update_file_info(self, file):
        uri = file.get_uri()
        if not uri.startswith('file://'): return
        
        path = unquote(uri.replace('file://', ''))
        
        try:
            status = os.getxattr(path, 'user.orchard.status').decode('utf-8')
            
            # Nemo specific attributes? 
            # Nemo uses emblems too.
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
            pass

    def get_file_items(self, window, files):
        if not files: return

        top_menuitem = Nemo.MenuItem(
            name='OrchardMenu',
            label='Orchard',
            tip='Orchard Sync Actions',
            icon='orchard-logo'
        )

        submenu = Nemo.Menu()
        top_menuitem.set_submenu(submenu)

        pin_item = Nemo.MenuItem(
            name='OrchardPin',
            label='Make Available Offline',
            tip='Download and keep this file on disk',
            icon='drive-harddisk'
        )
        pin_item.connect('activate', self.pin_action, files)
        submenu.append_item(pin_item)

        unpin_item = Nemo.MenuItem(
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
