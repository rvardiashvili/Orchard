#!/usr/bin/env python3
import os
import shutil
import sys
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

# Paths
HOME = Path.home()
NAUTILUS_EXT_DIR = HOME / ".local/share/nautilus-python/extensions"
NEMO_ACTION_DIR = HOME / ".local/share/nemo/actions"
DOLPHIN_SERVICE_DIR = HOME / ".local/share/kservices5/ServiceMenus"
THUNAR_CONFIG = HOME / ".config/Thunar/uca.xml"

SRC_DIR = Path(__file__).parent.parent / "src/extensions"

def install_nautilus():
    if not shutil.which("nautilus"): return
    print("Found Nautilus. Installing extension...")
    
    NAUTILUS_EXT_DIR.mkdir(parents=True, exist_ok=True)
    src = SRC_DIR / "nautilus/orchard_extension.py"
    dst = NAUTILUS_EXT_DIR / "orchard_extension.py"
    shutil.copy(src, dst)
    print(f"Installed to {dst}")
    print("To apply: nautilus -q")

def install_nemo():
    if not shutil.which("nemo"): return
    print("Found Nemo. Installing extensions...")
    
    # 1. Python Extension (Preferred for Emblems)
    NEMO_EXT_DIR = HOME / ".local/share/nemo-python/extensions"
    NEMO_EXT_DIR.mkdir(parents=True, exist_ok=True)
    
    src_py = SRC_DIR / "nemo/orchard_nemo_extension.py"
    dst_py = NEMO_EXT_DIR / "orchard_nemo_extension.py"
    shutil.copy(src_py, dst_py)
    print(f"Installed Python extension to {dst_py}")

    # 2. Nemo Actions (Fallback/Quick Access)
    NEMO_ACTION_DIR.mkdir(parents=True, exist_ok=True)
    for f in ["orchard-pin.nemo_action", "orchard-unpin.nemo_action"]:
        src = SRC_DIR / f"nemo/{f}"
        dst = NEMO_ACTION_DIR / f
        shutil.copy(src, dst)
        print(f"Installed Action {f}")
    
    print("To apply: nemo -q")

def install_dolphin():
    # Check for dolphin or kbuildsycoca5
    if not shutil.which("dolphin"): return
    print("Found Dolphin (KDE). Installing service menu...")
    
    # KDE paths can vary (kf5 vs kf6). Checking potential paths.
    candidates = [
        HOME / ".local/share/kio/servicemenus", # KDE 6 / modern
        HOME / ".local/share/kservices5/ServiceMenus", # KDE 5
    ]
    
    target_dir = None
    for c in candidates:
        if c.parent.exists(): # If .local/share/kio or kservices5 exists
            target_dir = c
            break
    
    if not target_dir: target_dir = candidates[0] # Default to modern
    
    target_dir.mkdir(parents=True, exist_ok=True)
    src = SRC_DIR / "dolphin/orchard.desktop"
    dst = target_dir / "orchard.desktop"
    shutil.copy(src, dst)
    os.chmod(dst, 0o755) # KDE requires executable
    print(f"Installed to {dst}")
    print("You may need to run: kbuildsycoca5 or kbuildsycoca6")

def install_thunar():
    if not shutil.which("thunar"): return
    print("Found Thunar. Injecting custom actions...")
    
    if not shutil.which("setfattr"):
        print("WARNING: 'setfattr' command not found. Thunar actions will be hidden. Install 'attr' package.")

    if not THUNAR_CONFIG.exists():
        print(f"Thunar config not found at {THUNAR_CONFIG}. Skipping.")
        return

    try:
        tree = ET.parse(THUNAR_CONFIG)
        root = tree.getroot()
        
        # Check if already installed
        for action in root.findall('action'):
            name = action.find('name')
            if name is not None and "Orchard" in name.text:
                print("Thunar actions already present. Skipping.")
                return

        # Define Actions
        actions = [
            ("Orchard: Make Available Offline", "setfattr -n user.orchard.pinned -v 1 %f", "drive-harddisk"),
            ("Orchard: Free Up Space", "setfattr -n user.orchard.pinned -v 0 %f", "drive-cloud")
        ]

        for name, cmd, icon in actions:
            action = ET.SubElement(root, 'action')
            ET.SubElement(action, 'icon').text = icon
            ET.SubElement(action, 'name').text = name
            ET.SubElement(action, 'submenu').text = ""
            ET.SubElement(action, 'unique-id').text = f"orchard-{icon}"
            ET.SubElement(action, 'command').text = cmd
            ET.SubElement(action, 'description').text = "Orchard Sync Control"
            ET.SubElement(action, 'patterns').text = "*"
            ET.SubElement(action, 'directories').text = "true" 
            ET.SubElement(action, 'audio-files').text = "true"
            ET.SubElement(action, 'image-files').text = "true"
            ET.SubElement(action, 'other-files').text = "true"
            ET.SubElement(action, 'text-files').text = "true"
            ET.SubElement(action, 'video-files').text = "true"
        
        tree.write(THUNAR_CONFIG)
        print("Updated uca.xml")
    except Exception as e:
        print(f"Failed to update Thunar config: {e}")

def install_icons():
    print("Installing Custom Icons...")
    
    # List of base icon directories to install to
    base_dirs = [
        HOME / ".local/share/icons/hicolor",
        HOME / ".icons"
    ]
    
    # Ensure they exist
    for base in base_dirs:
        if not base.exists() and base.name == ".icons":
            try: base.mkdir() 
            except: pass

    src_icons_root = Path(__file__).parent.parent / "src/assets/icons"
    if not src_icons_root.exists():
        print("Icon source root not found.")
        return

    for base in base_dirs:
        if not base.exists(): continue
        
        print(f"Installing to {base}...")
        
        target_emblem_dir = base / "scalable/emblems"
        target_app_dir = base / "scalable/apps"
        
        # Handle simple .icons folder vs hicolor structure
        if base.name == ".icons":
            target_emblem_dir = base
            target_app_dir = base
        else:
            target_emblem_dir.mkdir(parents=True, exist_ok=True)
            target_app_dir.mkdir(parents=True, exist_ok=True)

        for icon_path in src_icons_root.rglob("*.svg"): # Recursively find all SVGs
            relative_path = icon_path.relative_to(src_icons_root)
            # Determine destination based on top-level subdirectory
            if len(relative_path.parts) > 1: # Has a subdirectory
                top_level_dir = relative_path.parts[0]
                
                install_dir = None
                icon_type = ""
                if top_level_dir in ["app", "categories", "sidebar", "actions", "status"]:
                    install_dir = target_app_dir
                    icon_type = "App Icon"
                elif top_level_dir == "emblems":
                    install_dir = target_emblem_dir
                    icon_type = "Emblem"
                
                if install_dir:
                    shutil.copy(icon_path, install_dir / icon_path.name)
                    print(f"Installed {icon_type}: {icon_path.name} to {install_dir}")
                else:
                    print(f"Warning: Could not determine install location for {icon_path.name}")
            else: # Icons directly in src/assets/icons (should be empty after reorg)
                print(f"Warning: Icon {icon_path.name} found directly in root assets/icons. Consider moving it.")
                # Fallback to app_dir for any unclassified root icons
                shutil.copy(icon_path, target_app_dir / icon_path.name)

        # Update cache
        if shutil.which("gtk-update-icon-cache") and base.name != ".icons":
            subprocess.run(["gtk-update-icon-cache", "-f", "-t", str(base)], stderr=subprocess.DEVNULL)

    print("Icons installed. You may need to log out/in for changes to apply.")

def main():
    print("Installing File Manager Extensions for Orchard...")
    
    if not shutil.which("setfattr"):
        print("CRITICAL: 'attr' package is missing. Extensions require 'setfattr'.")
        print("Install via: sudo apt install attr  OR  sudo dnf install attr")
    
    install_icons()
    install_nautilus()
    install_nemo()
    install_dolphin()
    install_thunar()
    print("\nDone. Please restart your file manager.")

if __name__ == "__main__":
    main()
