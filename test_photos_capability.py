import sys
import os
from pathlib import Path

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from src.icloud_client.client import OrchardiCloudClient
from src.config.manager import ConfigManager

def test_photos_retrieve():
    config = ConfigManager()
    client = OrchardiCloudClient(config.apple_id, cookie_directory=config.cookie_dir)
    client.authenticate()

    svc = client._pyicloud_service.photos
    all_photos = svc.all
    
    # get the first photo element
    for photo in all_photos:
        print("First photo ID:", photo.id)
        
        # Now try to retrieve it directly, or see attributes array
        # Is there a way?
        # print("Dir of svc:", dir(svc))
        break

if __name__ == "__main__":
    test_photos_retrieve()
