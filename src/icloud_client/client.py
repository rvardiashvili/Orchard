# apple_api_reverse_eng_proj/orchard_icloud_client/client.py
import logging
import getpass # For secure password input
import keyring # For system keyring integration
import os
import shutil
from typing import Optional
from urllib.parse import urlparse

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

    def authenticate(self, input_callback=None):
        """
        Authenticates with iCloud using pyicloud.
        Handles 2FA/2SA prompts via CLI or optional callback.
        input_callback(type, message, options=None) -> str
        types: 'password', '2fa_code', 'device_select'
        """
        # Prompt for password if not available from init or keyring
        if self.password is None:
            if input_callback:
                self.password = input_callback("password", f"Enter password for {self.apple_id}")
            else:
                self.password = getpass.getpass(f"Enter password for {self.apple_id}: ")
            
            self._password_provided_by_user = True
            if not self.password: 
                LOGGER.error("Password cannot be empty.")
                self.authenticated = False
                return

        try:
            # FORCE FRESH SESSION IN INTERACTIVE MODE
            # If the user explicitly asks to verify/login (input_callback provided), 
            # we shouldn't rely on potentially stale/partial cookies.
            if input_callback:
                LOGGER.info("Interactive Auth: Forcing fresh session by wiping cookie directory...")
                try:
                    if os.path.exists(self.cookie_directory):
                        shutil.rmtree(self.cookie_directory)
                        LOGGER.info("Wiped cookie directory.")
                    os.makedirs(self.cookie_directory, exist_ok=True)
                except Exception as e:
                     LOGGER.warning(f"Error clearing session directory: {e}")

            # Initialize PyiCloudService with Retry
            for attempt in range(2):
                try:
                    print(f"DEBUG: Initializing PyiCloudService (Attempt {attempt+1})")
                    self._pyicloud_service = PyiCloudService(
                        self.apple_id,
                        self.password,
                        cookie_directory=self.cookie_directory,
                    )
                    break # Success
                except PyiCloudFailedLoginException as e:
                    print(f"DEBUG: Login failed on attempt {attempt+1}: {e}")
                    if attempt == 0 and input_callback:
                        # If first attempt fails (maybe changed password?), ask user
                        LOGGER.warning("Stored password may be invalid. Prompting user...")
                        new_pw = input_callback("password", f"Password for {self.apple_id} (Login Failed)")
                        if new_pw:
                            self.password = new_pw
                            self._password_provided_by_user = True
                            continue # Retry with new password
                    raise e # Re-raise if no callback or 2nd fail
            
            if self._pyicloud_service.requires_2fa or self._pyicloud_service.requires_2sa:
                LOGGER.warning("Two-Factor/Two-Step Authentication required.")
                self._handle_2fa(input_callback) 
            else:
                self.authenticated = True
                LOGGER.info(f"Successfully authenticated as {self.apple_id}")

            if self.authenticated and self._password_provided_by_user:
                self._save_password_to_keyring(self.password)
        except PyiCloudAuthRequiredException as e:
            LOGGER.error(f"Authentication required: {e}")
            self.authenticated = False
        except PyiCloudFailedLoginException:
            LOGGER.error("Failed to login to iCloud. Please check your credentials.")
            if self.password and not self._password_provided_by_user: 
                 try:
                     keyring.delete_password(KEYRING_SERVICE_NAME, self.apple_id)
                     LOGGER.info("Password removed from keyring due to failed login.")
                 except Exception as e:
                     LOGGER.warning(f"Failed to remove password from keyring after failed login: {e}")
            self.authenticated = False
        except Exception as e:
            LOGGER.error(f"An unexpected error occurred during authentication: {e}")
            self.authenticated = False

    def _handle_2fa(self, input_callback=None):
        """
        Handles the 2FA process by prompting the user for a code.
        """
        if not self._pyicloud_service:
            LOGGER.error("PyiCloudService not initialized for 2FA handling.")
            self.authenticated = False
            return

        if self._pyicloud_service.requires_2fa: 
            msg = "Two-factor authentication required. Enter the 6-digit code sent to your trusted device."
            if input_callback:
                code = input_callback("2fa_code", msg)
            else:
                print(msg)
                code = input("Code: ")
            
            try:
                result = self._pyicloud_service.validate_2fa_code(code)
                if result:
                    self.authenticated = True
                    LOGGER.info("2FA code validated successfully.")
                    
                    # CRITICAL: Trust this session to persist full access (FindMy/Settings)
                    try:
                        if not self._pyicloud_service.is_trusted_session:
                            self._pyicloud_service.trust_session()
                            LOGGER.info("Session marked as TRUSTED.")
                    except Exception as e:
                        LOGGER.warning(f"Failed to trust session: {e}")
                        
                else:
                    LOGGER.error("Failed to validate 2FA code.")
                    self.authenticated = False
            except Exception as e:
                LOGGER.error(f"Error during 2FA validation: {e}")
                self.authenticated = False
        elif self._pyicloud_service.requires_2sa: 
            print("Two-step verification required.")
            devices = self._pyicloud_service.trusted_devices
            
            if input_callback:
                # pass list of devices to GUI
                # options format: list of (index, label)
                options = [(i, f"{d.get('deviceName', 'Unknown')} ({d.get('osVersion', '?')})") for i, d in enumerate(devices)]
                selection = input_callback("device_select", "Choose a device to verify", options=options)
                try:
                    device_num = int(selection)
                except:
                    LOGGER.error("Invalid selection")
                    self.authenticated = False
                    return
            else:
                for i, device in enumerate(devices):
                    print(f"  {i}: {device.get('deviceName', 'Unknown Device')} ({device.get('osVersion', 'Unknown OS')})")
                try:
                    device_num = int(input("Please choose a device to send the verification code to: "))
                except:
                    return

            try:
                device = devices[device_num]
                result = self._pyicloud_service.send_verification_code(device)
                if result:
                    msg = "Please enter validation code:"
                    if input_callback:
                        code = input_callback("2fa_code", msg)
                    else:
                        code = input(msg + " ")
                        
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
            except Exception as e:
                LOGGER.error(f"Error during trusted device verification: {e}")
                self.authenticated = False
        else: 
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

    def _find_my_manual(self):
        """
        Manually initializes the Find My client via POST request.
        Strategy 1.5 from legacy implementation.
        """
        print("DEBUG: Entering _find_my_manual")
        if not self._pyicloud_service: 
            print("DEBUG: _pyicloud_service is None")
            return []
        
        try:
            webservices = self._pyicloud_service.data.get('webservices', {})
            if 'findme' not in webservices:
                print("DEBUG: 'findme' not in webservices")
                return []

            fmip_url = webservices['findme']['url']
            if fmip_url.endswith(':443'):
                fmip_url = fmip_url[:-4]
                
            init_url = f"{fmip_url}/fmipservice/client/web/initClient"
            
            # Headers needed for FindMy (Crucial!)
            headers = self.session.headers.copy()
            headers.update({
                'Origin': 'https://www.icloud.com',
                'Referer': 'https://www.icloud.com/',
            })
            
            payload = {
                "clientContext": {
                    "appName": "iCloud Find (Web)",
                    "appVersion": "2.0",
                    "timezone": "US/Pacific", 
                    "inactiveTime": 1,
                    "apiVersion": "3.0",
                    "fmly": True
                }
            }
            
            LOGGER.info(f"Manual FMIP POST to: {init_url}")
            print(f"DEBUG: POSTing to {init_url}")
            # Use self.session but override headers for this request
            res = self.session.post(init_url, json=payload, headers=headers)
            
            print(f"DEBUG: Response Status: {res.status_code}")
            if res.ok:
                data = res.json()
                content = data.get('content', [])
                print(f"DEBUG: Success. Content length: {len(content)}")
                dev_list = []
                for dev in content:
                    dev_list.append({
                        "name": dev.get('name', 'Unknown'),
                        "modelDisplayName": dev.get('deviceDisplayName', 'Apple Device'),
                        "deviceClass": dev.get('deviceClass', 'Unknown'), # or infer from icon/model
                        "batteryLevel": dev.get('batteryLevel', 'Unknown'), # Keep raw for consistency with other methods
                        "batteryStatus": dev.get('batteryStatus'),
                        "source": "fmip_manual"
                    })
                return dev_list
            else:
                LOGGER.warning(f"Manual FMIP POST failed: {res.status_code} {res.text[:100]}")
                print(f"DEBUG: Failed. Body: {res.text[:200]}")
                return []
                
        except Exception as e:
            LOGGER.error(f"Manual FMIP Exception: {e}")
            print(f"DEBUG: Exception in _find_my_manual: {e}")
            return []

    def login_with_passkey(self):
        """
        Attempts to initiate a Passkey (WebAuthn) login flow.
        This is experimental and mimics browser behavior.
        """
        if not self.session:
            # Need a session even if not logged in
            self._pyicloud_service = PyiCloudService(
                self.apple_id, 
                password="", 
                cookie_directory=self.cookie_directory
            )

        headers = self.session.headers.copy()
        headers.update({
            'Origin': 'https://www.icloud.com',
            'Referer': 'https://www.icloud.com/',
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/plain, */*',
        })
        
        data = {
            "accountName": self.apple_id,
            "protocols": ["webauthn"],
            "rememberMe": True
        }
        
        # We need the auth endpoint. PyiCloud usually determines this.
        # Default fallback:
        auth_endpoint = "https://idmsa.apple.com/appleauth/auth"
        if hasattr(self._pyicloud_service, '_auth_endpoint'):
            auth_endpoint = self._pyicloud_service._auth_endpoint

        url = f"{auth_endpoint}/signin/init"
        
        LOGGER.info(f"Initiating Passkey Login at {url}")
        try:
            resp = self.session.post(url, json=data, headers=headers)
            LOGGER.info(f"Passkey Init Response: {resp.status_code}")
            
            if resp.ok:
                body = resp.json()
                LOGGER.info(f"Passkey Data: {body}")
                return body
            else:
                LOGGER.error(f"Passkey Init Failed: {resp.text}")
                return None
        except Exception as e:
            LOGGER.error(f"Passkey Exception: {e}")
            return None

    def logout(self):
        """
        Wipes the session directory and clears authentication state.
        """
        LOGGER.info("Logging out and clearing session...")
        try:
            if os.path.exists(self.cookie_directory):
                shutil.rmtree(self.cookie_directory)
                os.makedirs(self.cookie_directory, exist_ok=True)
            self.authenticated = False
            self._pyicloud_service = None
            LOGGER.info("Logout complete.")
            return True
        except Exception as e:
            LOGGER.error(f"Logout failed: {e}")
            return False

    def get_account_info(self):
        """
        Returns structured account information from the session data.
        """
        if not self._pyicloud_service:
            return None
            
        ds_info = self._pyicloud_service.data.get('dsInfo', {})
        
        return {
            "full_name": ds_info.get("fullName"),
            "first_name": ds_info.get("firstName"),
            "last_name": ds_info.get("lastName"),
            "apple_id": ds_info.get("appleId"),
            "primary_email": ds_info.get("primaryEmail"),
            "locale": ds_info.get("locale"),
            "is_managed": ds_info.get("isManagedAppleID"),
            "hsa_version": ds_info.get("hsaVersion") # 2 = 2FA
        }

    def get_devices(self):
        """
        Returns the list of devices registered to the account.
        Priority:
        1. Standard FMIP (via pyicloud wrapper)
        2. Manual FMIP POST (fixes invalid header/auth issues)
        3. Trusted Devices (2FA list - limited data)
        4. Account Capabilities Fallback (Parsed from login data)
        """
        print("DEBUG: Entering get_devices")
        if not self._pyicloud_service:
            print("DEBUG: No service")
            return []

        dev_list = []
        errors = []


        try:
            manual_devs = self._find_my_manual()
            if manual_devs:
                LOGGER.info(f"Fetched {len(manual_devs)} devices from Manual FMIP")
                return manual_devs
        except Exception as e:
             LOGGER.warning(f"Manual FMIP fetch failed: {e}")
             print(f"DEBUG: Strat 2 Failed: {e}")
             errors.append(f"FMIP_Man: {str(e)}")

        if errors:
            LOGGER.error(f"All device fetch strategies failed: {errors}")
            return []
            
        return []

    def get_storage_usage(self):
        """
        Fetches storage usage info from the setup service.
        """
        setup_url = self.get_webservice_url("setup")
        
        # Fallback if 'setup' service is not explicitly listed
        if not setup_url:
            # Try to construct from drivews
            drive_url = self.get_webservice_url("drivews")
            if drive_url:
                try:
                    # drive_url example: https://p51-drivews.icloud.com/retrieve/drive/v1/
                    # We want: https://p51-setup.icloud.com/setup/ws/1
                    parsed = urlparse(drive_url)
                    if "drivews" in parsed.netloc:
                        new_netloc = parsed.netloc.replace("drivews", "setup")
                        setup_url = f"{parsed.scheme}://{new_netloc}/setup/ws/1"
                        LOGGER.info(f"Constructed fallback setup URL: {setup_url}")
                except Exception as e:
                    LOGGER.warning(f"Failed to construct fallback URL: {e}")

        if not setup_url:
            LOGGER.warning("Setup service URL not found.")
            return None

        url = f"{setup_url}/storageUsageInfo"
        
        # Merge default params with specific ones
        # Using the params provided by the user + existing session params
        params = {
            'clientBuildNumber': '2546Build34',
            'clientMasteringNumber': '2546Build34',
            'clientId': self.session.params.get('clientId'),
            'dsid': self.session.params.get('dsid'),
        }

        try:
            response = self.session.post(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            LOGGER.error(f"Failed to fetch storage usage: {e}")
            return None

