import sys
import getpass
import logging
from pyicloud import PyiCloudService
from pyicloud.exceptions import PyiCloudFailedLoginException, PyiCloud2FARequiredException

import os

logger = logging.getLogger(__name__)

class AuthManager:
    def __init__(self, username=None, cookie_dir=None):
        self.username = username
        self.api = None
        
        # Determine persistent cookie directory
        if cookie_dir:
            self.cookie_dir = os.path.expanduser(cookie_dir)
        else:
            self.cookie_dir = os.path.expanduser("~/.config/icloud_sync")
            
        if not os.path.exists(self.cookie_dir):
            os.makedirs(self.cookie_dir, exist_ok=True)

    def login(self):
        """
        Attempt to login to iCloud.
        If 2FA is required, it will prompt via stdin.
        """
        if not self.username:
            self.username = input("Enter iCloud Username (Email): ")

        print(f"Logging in as {self.username}...")
        
        # 1. Try to login (relying on keyring or session)
        try:
            self.api = PyiCloudService(self.username, cookie_directory=self.cookie_dir)
        except (PyiCloudFailedLoginException, Exception) as e:
            # If that fails (e.g. no password in keyring), prompt user
            logger.info("Saved credential not found or invalid. Requesting password...")
            password = getpass.getpass("Enter iCloud Password: ")
            try:
                self.api = PyiCloudService(self.username, password=password, cookie_directory=self.cookie_dir)
            except PyiCloudFailedLoginException:
                logger.error("Login failed. Invalid credentials.")
                sys.exit(1)

        if self.api.requires_2fa:
            self._handle_2fa()
        
        logger.info("Authentication successful.")
        return self.api

    def _handle_2fa(self):
        print("Two-factor authentication required.")
        code = input("Enter the code you received on your device: ")
        
        result = self.api.validate_2fa_code(code)
        print(f"Code validation result: {result}")

        if not result:
            print("Failed to verify 2FA code.")
            sys.exit(1)

        if not self.api.is_trusted_session:
            print("Session is not trusted. Requesting trust...")
            try:
                self.api.trust_session()
                print("Session trusted.")
            except Exception as e:
                logger.warning(f"Could not trust session: {e}")

    def get_service(self):
        if not self.api:
            self.login()
        return self.api

if __name__ == "__main__":
    # Test authentication
    logging.basicConfig(level=logging.INFO)
    auth = AuthManager()
    auth.login()
    print(f"Welcome, {auth.api.user.get('fullName')}")
