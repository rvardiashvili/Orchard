# apple_api_reverse_eng_proj/orchard_icloud_client/client.py
import logging
import getpass # For secure password input
import keyring # For system keyring integration
from typing import Optional

from pyicloud import PyiCloudService
from pyicloud.exceptions import (
    PyiCloud2FARequiredException,
    PyiCloudAuthRequiredException,
    PyiCloudFailedLoginException,
)

LOGGER = logging.getLogger(__name__)

# Service name for keyring storage
KEYRING_SERVICE_NAME = "Orchard-iCloud"

class OrchardiCloudClient:
    """
    Custom client for iCloud communications.
    Uses pyicloud for authentication and session management only.
    """

    def __init__(self, apple_id: str, password: Optional[str] = None, cookie_directory: Optional[str] = None):
        self.apple_id = apple_id
        self.password = password
        self.cookie_directory = cookie_directory
        self._pyicloud_service: Optional[PyiCloudService] = None
        self.authenticated = False
        self._password_provided_by_user = False # Track if password was initially passed or prompted

        # If no password provided, try to get it from keyring
        if self.password is None:
            self.password = self._get_password_from_keyring()
            if self.password:
                LOGGER.info("Password retrieved from system keyring.")
        else:
            self._password_provided_by_user = True # User provided password, might need saving

    def _get_password_from_keyring(self) -> Optional[str]:
        """Retrieve password from system keyring."""
        try:
            return keyring.get_password(KEYRING_SERVICE_NAME, self.apple_id)
        except Exception as e:
            LOGGER.warning(f"Could not retrieve password from keyring: {e}")
            return None

    def _save_password_to_keyring(self, password: str):
        """Save password to system keyring."""
        try:
            keyring.set_password(KEYRING_SERVICE_NAME, self.apple_id, password)
            LOGGER.info("Password saved to system keyring.")
        except Exception as e:
            LOGGER.error(f"Failed to save password to keyring: {e}")

    def authenticate(self):
        """
        Authenticates with iCloud using pyicloud.
        Handles 2FA/2SA prompts.
        """
        # Prompt for password if not available from init or keyring
        if self.password is None:
            self.password = getpass.getpass(f"Enter password for {self.apple_id}: ")
            self._password_provided_by_user = True
            if not self.password: # User might just press Enter
                LOGGER.error("Password cannot be empty.")
                self.authenticated = False
                return

        try:
            # Initialize PyiCloudService. This might succeed even if 2FA is required,
            # as it internally sets flags like requires_2fa.
            self._pyicloud_service = PyiCloudService(
                self.apple_id,
                self.password,
                cookie_directory=self.cookie_directory,
            )
            
            # Check for 2FA/2SA *after* initialization
            # Use requires_2sa here, as requires_trusted_device_verification is not a direct attribute
            if self._pyicloud_service.requires_2fa or self._pyicloud_service.requires_2sa:
                LOGGER.warning("Two-Factor/Two-Step Authentication required.")
                self._handle_2fa() # This will set self.authenticated if successful
            else:
                # If no 2FA/2SA needed, then authentication is complete
                self.authenticated = True
                LOGGER.info(f"Successfully authenticated as {self.apple_id}")

            # Save password to keyring if it was provided by user during this session
            # and authentication was successful (including 2FA/2SA)
            if self.authenticated and self._password_provided_by_user:
                self._save_password_to_keyring(self.password)
        except PyiCloudAuthRequiredException as e:
            LOGGER.error(f"Authentication required: {e}")
            self.authenticated = False
        except PyiCloudFailedLoginException:
            LOGGER.error("Failed to login to iCloud. Please check your credentials.")
            # Clear password from keyring on failed login if it was used/provided
            if self.password and not self._password_provided_by_user: # Only if fetched from keyring
                 try:
                     keyring.delete_password(KEYRING_SERVICE_NAME, self.apple_id)
                     LOGGER.info("Password removed from keyring due to failed login.")
                 except Exception as e:
                     LOGGER.warning(f"Failed to remove password from keyring after failed login: {e}")
            self.authenticated = False
        except Exception as e:
            LOGGER.error(f"An unexpected error occurred during authentication: {e}")
            self.authenticated = False

    def _handle_2fa(self):
        """
        Handles the 2FA process by prompting the user for a code.
        """
        if not self._pyicloud_service:
            LOGGER.error("PyiCloudService not initialized for 2FA handling.")
            self.authenticated = False
            return

        if self._pyicloud_service.requires_2fa: # This handles modern 2FA (6-digit code)
            print("Two-factor authentication required.")
            code = input("Enter the 6-digit code sent to your trusted device: ")
            try:
                result = self._pyicloud_service.validate_2fa_code(code)
                if result:
                    self.authenticated = True
                    LOGGER.info("2FA code validated successfully.")
                else:
                    LOGGER.error("Failed to validate 2FA code.")
                    self.authenticated = False
            except Exception as e:
                LOGGER.error(f"Error during 2FA validation: {e}")
                self.authenticated = False
        elif self._pyicloud_service.requires_2sa: # This handles older 2SA or trusted device verification
            print("Two-step verification required.")
            devices = self._pyicloud_service.trusted_devices
            for i, device in enumerate(devices):
                print(f"  {i}: {device.get('deviceName', 'Unknown Device')} ({device.get('osVersion', 'Unknown OS')})")
            try:
                device_num = int(input("Please choose a device to send the verification code to: "))
                device = devices[device_num]
                result = self._pyicloud_service.send_verification_code(device)
                if result:
                    code = input("Please enter validation code: ")
                    validation_result = self._pyicloud_service.validate_verification_code(device, code)
                    if validation_result:
                        self.authenticated = True
                        LOGGER.info("Verification code validated successfully.")
                    else:
                        LOGGER.error("Failed to validate verification code.")
                        self.authenticated = False
                else:
                    LOGGER.error("Failed to send verification code.")
                    self.authenticated = False
            except (ValueError, IndexError):
                LOGGER.error("Invalid device selection.")
                self.authenticated = False
            except Exception as e:
                LOGGER.error(f"Error during trusted device verification: {e}")
                self.authenticated = False
        else: # Fallback if neither is true but still not authenticated
            LOGGER.error("Authentication requires further interaction not handled by simple 2FA/2SA checks.")
            self.authenticated = False


    @property
    def session(self):
        """
        Returns the underlying requests.Session object from pyicloud.
        This session is authenticated and will be used for raw API calls.
        """
        if self._pyicloud_service and self._pyicloud_service.session:
            return self._pyicloud_service.session
        return None

    @property
    def webservices(self):
        """
        Returns the webservices dictionary from pyicloud, which contains API URLs.
        """
        if self._pyicloud_service:
            return self._pyicloud_service.data.get('webservices')
        return None

    def get_webservice_url(self, ws_key: str) -> Optional[str]:
        """
        Helper to get a webservice URL using pyicloud's method.
        """
        if self._pyicloud_service:
            try:
                return self._pyicloud_service.get_webservice_url(ws_key)
            except Exception as e:
                LOGGER.error(f"Failed to get webservice URL for {ws_key}: {e}")
        return None

