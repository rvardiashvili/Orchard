import os
import shutil
import logging
import datetime

logger = logging.getLogger(__name__)

# Default backup paths
DOTFILES = [
    ".bashrc",
    ".zshrc",
    ".profile",
    ".vimrc",
    ".config/i3/config",
    ".config/nvim/init.vim",
    ".gitconfig"
]

def backup_dotfiles(sync_root):
    """
    Backs up critical dotfiles to iCloud/LinuxBackups.
    """
    backup_dir = os.path.join(sync_root, "LinuxBackups")
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)

    home = os.path.expanduser("~")
    timestamp = datetime.datetime.now().strftime("%Y%m%d")
    
    logger.info(f"Starting Backup to {backup_dir}...")
    
    count = 0
    for dot in DOTFILES:
        src = os.path.join(home, dot)
        if os.path.exists(src):
            # Flat structure: .bashrc -> bashrc_20230101.bak
            safe_name = dot.replace("/", "_").lstrip(".")
            dst_name = f"{safe_name}_{timestamp}.bak"
            dst = os.path.join(backup_dir, dst_name)
            
            try:
                shutil.copy2(src, dst)
                count += 1
            except Exception as e:
                logger.error(f"Failed to backup {dot}: {e}")
    
    logger.info(f"Backup Complete. {count} files saved.")
    return count, backup_dir
