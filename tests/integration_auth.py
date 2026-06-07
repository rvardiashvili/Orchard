import os
import logging
from pathlib import Path
import getpass # Import getpass for manual Apple ID input

from orchard_icloud_client.client import OrchardiCloudClient
from pyicloud.exceptions import PyiCloudFailedLoginException

# Set up basic logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
LOGGER = logging.getLogger(__name__)

def run_live_authentication():
    """
    Runs a live authentication against iCloud, persisting the session.
    """
    print("--- iCloud Live Authentication Script ---")

    script_dir = Path(__file__).parent
    cookie_dir = script_dir / ".icloud_session"
    cookie_dir.mkdir(parents=True, exist_ok=True)
    print(f"Session data (cookies, tokens) will be stored in: {cookie_dir}")

    # Prompt for Apple ID only, password handled by OrchardiCloudClient via keyring or getpass
    apple_id = input("Enter your Apple ID: ")
    if not apple_id:
        LOGGER.error("Apple ID cannot be empty.")
        return

    client = OrchardiCloudClient(
        apple_id=apple_id,
        password=None, # Will be fetched from keyring or prompted by OrchardiCloudClient
        cookie_directory=str(cookie_dir)
    )

    print("Attempting to authenticate...")
    client.authenticate()

    if client.authenticated:
        LOGGER.info("Authentication successful! Session data has been stored.")
        # Perform a very basic test interaction with iCloud Drive
        # to confirm the session is usable.
        drive_ws_url = client.get_webservice_url("drivews")
        if drive_ws_url:
            LOGGER.info(f"iCloud Drive webservice URL: {drive_ws_url}")
            LOGGER.info("Session appears to be active and capable of fetching service URLs.")
        else:
            LOGGER.warning("Could not retrieve iCloud Drive webservice URL. Session might be limited.")
    else:
        LOGGER.error("Authentication failed. Please check the logs for details.")

    print("\n--- Script Finished ---")

if __name__ == "__main__":
    run_live_authentication()
