import sys
from pathlib import Path
import json

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))
from src.icloud_client.client import OrchardiCloudClient
from src.config.manager import ConfigManager

def main():
    config = ConfigManager()
    client = OrchardiCloudClient(config.apple_id, cookie_directory=config.cookie_dir)
    client.authenticate()
    for photo in client._pyicloud_service.photos.all:
        print("Type:", type(photo))
        print("Dir:", dir(photo))
        print("Asset record:", hasattr(photo, '_asset_record'))
        print("Master record:", hasattr(photo, '_master_record'))
        break

if __name__ == "__main__": main()
