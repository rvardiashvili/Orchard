import logging
import mimetypes
import os
import uuid
import json
from typing import Any, Dict, List, Optional
from requests import Session, Response
from pyicloud.exceptions import PyiCloudAPIResponseException

LOGGER = logging.getLogger(__name__)

# Constants from pyicloud.services.drive
CLOUD_DOCS_ZONE = "com.apple.CloudDocs"
NODE_ROOT = "root"
CLOUD_DOCS_ZONE_ID_ROOT = f"FOLDER::{CLOUD_DOCS_ZONE}::{NODE_ROOT}"
TRASH_ROOT_ID = "TRASH_ROOT"

class iCloudDrive:
    """
    Manages interactions with iCloud Drive.
    """
    def __init__(self, session: Session, service_root: str, document_root: str, params: Dict[str, Any]):
        if not session:
            raise ValueError("Authenticated requests.Session is required.")
        if not service_root:
            raise ValueError("iCloud Drive service_root URL is required.")
        if not document_root:
            raise ValueError("iCloud Drive document_root URL is required.")
        if not params:
            raise ValueError("iCloud Drive params dictionary is required.")

        self._session = session
        self._service_root = service_root
        self._document_root = document_root
        self._params = params

    def _raise_if_error(self, response: Response) -> None:
        """Helper to raise an exception if the response indicates an error."""
        if not response.ok:
            error_message = response.reason or "Unknown iCloud API error"
            LOGGER.error(f"iCloud Drive API Error {response.status_code}: {error_message}")
            raise Exception(f"iCloud Drive API Error {response.status_code}: {error_message}")

    def _ensure_prefix(self, item_id: str, item_type: str = "FILE") -> str:
        """
        Ensures the item_id has the correct prefix (e.g., FILE::com.apple.CloudDocs::UUID).
        If item_id is already prefixed or is a special root, returns it as is.
        """
        if not item_id:
            return item_id
        if item_id == NODE_ROOT:
            return CLOUD_DOCS_ZONE_ID_ROOT
        if item_id == TRASH_ROOT_ID:
            return TRASH_ROOT_ID
        if "::" in item_id:
            return item_id
        # Construct the prefix based on type
        return f"{item_type}::{CLOUD_DOCS_ZONE}::{item_id}"

    def get_item_metadata(self, item_id: str, parent_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Fetch metadata for a single CloudDocs item.
        If parent_id is provided, it lists the parent directory to find the item (reliable).
        If parent_id is missing, it falls back to ID-only lookup (unreliable).
        """
        if parent_id:
            LOGGER.info(f"Fetching metadata for item_id={item_id} via parent_id={parent_id}")
            try:
                children = self.list_directory(parent_id)
                for child in children:
                    # Match against docwsid or drivewsid
                    # child['drivewsid'] is typically "FILE::...::UUID" or "FOLDER::...::UUID"
                    # item_id might be just UUID or full ID.
                    
                    c_drivewsid = child.get('drivewsid')
                    c_docwsid = child.get('docwsid')
                    
                    if self._ids_match(child, item_id):
                        return {
                            "cloud_id": c_docwsid if child.get('type') == 'FILE' else c_drivewsid,
                            "etag": child.get('etag'),
                            "name": child.get('name'),
                            "extension": child.get('extension'),
                            "size": child.get('size'),
                            "type": child.get('type'),
                            "parentId": parent_id, # We know the parent since we listed it
                            "modified": child.get('dateModified'), # list_directory returns dateModified/dateChanged
                            "created": child.get('dateCreated'),
                        }
                LOGGER.warning(f"Item {item_id} not found in parent {parent_id}")
                return None
            except Exception as e:
                LOGGER.error(f"Failed to list parent {parent_id} for metadata lookup: {e}")
                # Fallthrough to direct lookup? Or raise?
                # User says direct lookup doesn't work. Let's try it as fallback anyway?
                pass

        # Fallback to direct lookup (User says this is broken/unreliable, but we keep it for now if parent_id is unknown)
        # Normalize CloudDocs ID
        document_id = item_id
        if "::" in item_id:
            document_id = item_id.split("::")[-1]

        LOGGER.info(f"Lookup metadata (direct fallback) for item_id={document_id}")

        payload = {
            "documents": [
                {"documentId": document_id}
            ]
        }

        response = self._session.post(
            f"{self._document_root}/docws/lookup",
            params=self._params,
            json=payload,
        )

        # Explicit status handling
        if response.status_code == 404:
            return None  # item deleted or inaccessible

        if response.status_code == 204:
            return None  # no content = gone

        self._raise_if_error(response)

        # Guard: response may be empty or non-JSON
        if not response.content:
            LOGGER.warning(f"No content returned for item {document_id}")
            return None

        try:
            data = response.json()
        except ValueError:
            LOGGER.error(f"Non-JSON response for item {document_id}: {response.text[:200]}")
            raise

        docs = data.get("documents")
        if not docs:
            return None

        doc = docs[0]

        return {
            "cloud_id": doc.get("documentId"),
            "etag": doc.get("etag"),
            "name": doc.get("name"),
            "extension": doc.get("extension"),
            "size": doc.get("size"),
            "type": doc.get("type"),  # FILE / FOLDER
            "parentId": doc.get("parentId"),
            "modified": doc.get("modified"),
            "created": doc.get("created"),
        }
    def _ids_match(self, item, search_id):
        # Helper to match ID against docwsid, drivewsid, or bare UUID
        if item.get('docwsid') == search_id: return True
        if item.get('drivewsid') == search_id: return True
        
        # Check suffixes if search_id is bare UUID
        if "::" not in search_id:
            if item.get('docwsid', '').endswith(f"::{search_id}"): return True
            if item.get('drivewsid', '').endswith(f"::{search_id}"): return True
        return False

    def list_directory(self, folder_id: Optional[str] = None) -> List[Dict[str, Any]]:
        target_folder_id = folder_id if folder_id is not None else CLOUD_DOCS_ZONE_ID_ROOT
        target_folder_id = self._ensure_prefix(target_folder_id, "FOLDER")
        
        LOGGER.info(f"Listing directory for folder_id: {target_folder_id}")

        request_data = [{"drivewsid": target_folder_id, "partialData": False}]
        
        try:
            response = self._session.post(
                f"{self._service_root}/retrieveItemDetailsInFolders",
                params=self._params,
                json=request_data,
            )
            self._raise_if_error(response)
            
            items_data = response.json()
            if items_data and isinstance(items_data, list) and len(items_data) > 0:
                folder_details = items_data[0]
                if "items" in folder_details:
                    return folder_details["items"]
                else:
                    return [folder_details]
            return []
        except Exception as e:
            LOGGER.error(f"Error listing directory: {e}")
            raise

    def list_trash(self) -> List[Dict[str, Any]]:
        LOGGER.info("Listing Trash")
        request_data = [{
            "drivewsid": TRASH_ROOT_ID,
            "partialData": False,
            "includeHierarchy": True
        }]
        
        try:
            response = self._session.post(
                f"{self._service_root}/retrieveItemDetailsInFolders",
                params=self._params,
                json=request_data,
            )
            self._raise_if_error(response)
            
            items_data = response.json()
            if items_data and isinstance(items_data, list) and len(items_data) > 0:
                folder_details = items_data[0]
                if "items" in folder_details:
                    return folder_details["items"]
            return []
        except Exception as e:
            LOGGER.error(f"Error listing trash: {e}")
            raise

    def download_file(self, file_id: str, zone: str = CLOUD_DOCS_ZONE, local_path: Optional[str] = None) -> str:
        LOGGER.info(f"Attempting to download file with ID: {file_id}")

        document_id = file_id
        if "::" in file_id:
            document_id = file_id.split("::")[-1]

        file_params = dict(self._params)
        file_params.update({"document_id": document_id})

        # Step 1: Get the download URL
        try:
            response = self._session.get(
                f"{self._document_root}/ws/{zone}/download/by_id",
                params=file_params,
            )
            self._raise_if_error(response)
            response_json = response.json()
        except Exception as e:
            raise Exception(f"Failed to get download URL for {file_id}") from e

        download_url = None
        if "data_token" in response_json and "url" in response_json["data_token"]:
            download_url = response_json["data_token"]["url"]
        elif "package_token" in response_json and "url" in response_json["package_token"]:
            download_url = response_json["package_token"]["url"]
        
        if not download_url:
            raise Exception(f"Could not find download URL for file {file_id}")

        # Determine filename and local path
        if local_path is None:
            # Fallback logic since we might not know parent_id here in CLI usage
            # But if SyncEngine calls this, it usually provides local_path derived from DB.
            filename_from_url = os.path.basename(download_url.split('?')[0].split('%3F')[0])
            local_path = filename_from_url
            LOGGER.warning("Using URL filename because parent_id for metadata fetch is unknown in this context.")

        # Step 2: Download
        LOGGER.info(f"Downloading from {download_url} to {local_path}")
        temp_path = f"{local_path}.part"
        try:
            file_content_response = self._session.get(download_url, stream=True)
            self._raise_if_error(file_content_response)
            with open(temp_path, 'wb') as f:
                for chunk in file_content_response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            # Atomic move
            os.rename(temp_path, local_path)
            return local_path
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise Exception(f"Failed to save downloaded file {file_id}") from e

    def download_directory(self, folder_id: str, local_path: str):
        full_folder_id = self._ensure_prefix(folder_id, "FOLDER")
        LOGGER.info(f"Downloading directory {full_folder_id} to {local_path}")
        
        if not os.path.exists(local_path):
            os.makedirs(local_path)
        
        contents = self.list_directory(full_folder_id)
        for item in contents:
            name = item.get('name')
            if not name: continue
            
            # Sanitize name for local fs
            safe_name = name.replace('/', '_') 
            item_local_path = os.path.join(local_path, safe_name)
            
            item_type = item.get('type')
            
            if item_type == 'FILE':
                ext = item.get('extension')
                if ext and not safe_name.endswith(f".{ext}"):
                    item_local_path += f".{ext}"
                file_id = item.get('docwsid', item.get('drivewsid'))
                self.download_file(file_id, local_path=item_local_path)
                
            elif item_type == 'FOLDER':
                self.download_directory(item['drivewsid'], item_local_path)

    def rename_item(self, item_id: str, etag: str, new_name: str) -> Dict[str, Any]:
        drivewsid = self._ensure_prefix(item_id)
        LOGGER.info(f"Renaming item ID: {drivewsid} to '{new_name}'")
        request_data = {
            "items": [{
                "drivewsid": drivewsid,
                "etag": etag,
                "name": new_name,
                "clientId": self._params.get("clientId", self._params.get("client_id")),
            }]
        }

        try:
            response = self._session.post(
                f"{self._service_root}/renameItems",
                params=self._params,
                json=request_data,
            )
            self._raise_if_error(response)
            return response.json()
        except Exception as e:
            raise Exception(f"Failed to rename item {item_id}") from e

    def delete_item(self, item_id: str, etag: str) -> Dict[str, Any]:
        drivewsid = self._ensure_prefix(item_id)
        LOGGER.info(f"Deleting item ID: {drivewsid}")
        request_data = {
            "items": [{
                "drivewsid": drivewsid,
                "etag": etag,
                "clientId": self._params.get("clientId", self._params.get("client_id")),
            }]
        }

        try:
            response = self._session.post(
                f"{self._service_root}/deleteItems",
                params=self._params,
                json=request_data,
            )
            self._raise_if_error(response)
            return response.json()
        except Exception as e:
            raise Exception(f"Failed to delete item {item_id}") from e

    def recover_item(self, item_id: str, etag: str) -> Dict[str, Any]:
        drivewsid = self._ensure_prefix(item_id)
        LOGGER.info(f"Recovering item ID: {drivewsid} from Trash")
        request_data = {
            "items": [{
                "drivewsid": drivewsid,
                "etag": etag
            }]
        }

        try:
            response = self._session.post(
                f"{self._service_root}/putBackItemsFromTrash",
                params=self._params,
                json=request_data,
            )
            self._raise_if_error(response)
            return response.json()
        except Exception as e:
            raise Exception(f"Failed to recover item {item_id}") from e

    def upload_file(self, local_path: str, parent_folder_id: str, remote_name: Optional[str] = None) -> Dict[str, Any]:
        import requests 

        # Cleanup ID: If it's the complex ID, try to simplify it for the JSON payload
        # The web client sends "root" or the bare UUID for the parent.
        if parent_folder_id == CLOUD_DOCS_ZONE_ID_ROOT:
            simple_parent_id = "root"
        elif "::" in parent_folder_id:
            simple_parent_id = parent_folder_id.split("::")[-1]
        else:
            simple_parent_id = parent_folder_id

        LOGGER.info(f"Uploading '{local_path}' to parent: {simple_parent_id}")

        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        file_size = os.path.getsize(local_path)
        filename = remote_name if remote_name else os.path.basename(local_path)
        
        # 1. Force strict MIME type
        content_type, _ = mimetypes.guess_type(filename)
        if content_type is None:
            content_type = "application/octet-stream"

        # --- STEP 1: Authorize (Get Upload URL) ---
        LOGGER.info("Step 1/3: Requesting upload authorization...")
        upload_req_data = {
            "filename": filename,
            "type": "FILE",
            "content_type": content_type,
            "size": file_size,
        }

        try:
            response = self._session.post(
                f"{self._document_root}/ws/{CLOUD_DOCS_ZONE}/upload/web",
                params=self._params,
                json=upload_req_data,
            )
            self._raise_if_error(response)
            upload_info = response.json()[0]
            document_id = upload_info["document_id"]
            upload_url = upload_info["url"]
        except Exception as e:
            LOGGER.error(f"Step 1 Failed: {e}")
            raise

        # --- STEP 2: Transport (Send Content) ---
        LOGGER.info(f"Step 2/3: Transporting {file_size} bytes to storage server...")
        
        try:
            with open(local_path, 'rb') as f:
                headers = {
                    "User-Agent": self._session.headers.get("User-Agent", "Mozilla/5.0"),
                    "Accept": "*/*",
                    "Connection": "keep-alive",
                    "Content-Length": str(file_size),
                    "Origin": "https://www.icloud.com",
                    "Referer": "https://www.icloud.com/"
                }

                files_payload = {
                    "files": (filename, f, content_type)
                }

                content_response = requests.post(
                    upload_url, 
                    files=files_payload,
                    headers=headers,
                    timeout=300
                )
                
            if not content_response.ok:
                LOGGER.error(f"Upload Server Rejected: {content_response.status_code}")
                raise Exception(f"Upload server rejected file: {content_response.status_code}")
                
            file_info = content_response.json()["singleFile"]
        except Exception as e:
            LOGGER.error(f"Step 2 Failed: {e}")
            raise

        # --- STEP 3: Finalize (Link File) ---
        LOGGER.info("Step 3/3: Finalizing and linking file...")
        
        # Helper to get current time in MS
        import time
        now_ms = int(time.time() * 1000)

        # Construct the 'data' dictionary exactly as seen in your log
        inner_data = {
            "signature": file_info["fileChecksum"],
            "wrapping_key": file_info["wrappingKey"],
            "reference_signature": file_info["referenceChecksum"],
            "size": file_info["size"],
        }
        if "receipt" in file_info:
            inner_data["receipt"] = file_info["receipt"]

        # Construct the final payload.
        # REMOVED: "create_short_guid": True (This was likely the cause of 412)
        finalize_data = {
            "allow_conflict": True,
            "btime": now_ms,
            "command": "add_file",
            "data": inner_data,
            "document_id": document_id,
            "file_flags": {
                "is_writable": True,
                "is_executable": False,
                "is_hidden": False,
            },
            "mtime": now_ms,
            "path": {
                "starting_document_id": simple_parent_id, # "root" or UUID
                "path": filename,
            }
        }

        try:
            # Send as text/plain to avoid CORS/Preflight header issues, matching web behavior
            json_payload = json.dumps(finalize_data, separators=(',', ':'))

            headers = {
                "Content-Type": "text/plain",
                "Origin": "https://www.icloud.com",
                "Referer": "https://www.icloud.com/",
                "Cookie": "; ".join([f"{k}={v}" for k, v in self._session.cookies.items()]) # Manually pass cookies
            }

            response = requests.post(
                f"{self._document_root}/ws/{CLOUD_DOCS_ZONE}/update/documents",
                params=self._params,
                data=json_payload,
                headers=headers
            )
            
            if not response.ok:
                LOGGER.error(f"Step 3 Failed: {response.status_code}")
                LOGGER.error(f"Response: {response.text}")
                response.raise_for_status()

            LOGGER.info(f"Upload Successful. Doc ID: {document_id}")
            
            # Parse response to ensure we return a useful dict with document_id
            try:
                resp_data = response.json()
                if isinstance(resp_data, list) and len(resp_data) > 0:
                    resp_data = resp_data[0]
                
                if isinstance(resp_data, dict):
                    # Ensure document_id is present (it might be 'docwsid' or missing)
                    if 'document_id' not in resp_data:
                        resp_data['document_id'] = document_id
                    return resp_data
                else:
                    # Fallback for unexpected format
                    return {"document_id": document_id, "raw_response": resp_data}
            except Exception:
                # Fallback if JSON parsing fails
                return {"document_id": document_id}

        except Exception as e:
            LOGGER.error(f"Step 3 Failed: {e}")
            raise

    def move_item(self, item_id: str, etag: str, new_parent_folder_id: str) -> Dict[str, Any]:
        drivewsid = self._ensure_prefix(item_id)
        dest_id = self._ensure_prefix(new_parent_folder_id, "FOLDER")
        
        # Use existing clientId from params if available (persistent), otherwise generate new one?
        # Re-using clientId from params might cause issues if it's treated as a transaction ID.
        # But delete_items uses params['clientId'].
        # Let's try generating a new one if it fails, or maybe just use the item_id like trash?
        # Strategy: Use params['clientId'] first, similar to delete_item.
        client_id = self._params.get("clientId", self._params.get("client_id"))
        if not client_id:
            client_id = str(uuid.uuid4())

        LOGGER.info(f"Moving item {drivewsid} to {dest_id}")
        request_data = {
                "destinationDrivewsId": dest_id,
            "items": [{
                "drivewsid": drivewsid,
                "etag": etag,
                "clientId": client_id
            }]
        }
        
        try:
            response = self._session.post(
                f"{self._service_root}/moveItems",
                params=self._params,
                json=request_data,
            )
            self._raise_if_error(response)
            return response.json()
        except Exception as e:
            # If it's a PyiCloudAPIResponseException, the response object might be lost if not attached.
            # But we can try to log what we have.
            LOGGER.error(f"Failed to move item. Payload: {json.dumps(request_data)}")
            if hasattr(e, 'response') and e.response: # If exception has response attached
                 LOGGER.error(f"Response: {e.response.text}")
            raise Exception(f"Failed to move item {item_id}") from e

    def copy_item(self, item_id: str, etag: str, new_parent_folder_id: str) -> Dict[str, Any]:
        drivewsid = self._ensure_prefix(item_id)
        dest_id = self._ensure_prefix(new_parent_folder_id, "FOLDER")
        
        LOGGER.info(f"Copying item {drivewsid} to {dest_id}")
        request_data = {
            "items": [{
                "drivewsid": drivewsid,
                "etag": etag,
                "destinationDrivewsId": dest_id,
                "clientId": str(uuid.uuid4()) # Copy requires a new client ID to avoid dedup
            }]
        }
        
        try:
            response = self._session.post(
                f"{self._service_root}/copyItems",
                params=self._params,
                json=request_data,
            )
            self._raise_if_error(response)
            return response.json()
        except Exception as e:
            raise Exception(f"Failed to copy item {item_id}") from e

    def create_folder(self, parent_folder_id: str, folder_name: str) -> Dict[str, Any]:
        full_parent_id = self._ensure_prefix(parent_folder_id, "FOLDER")
        LOGGER.info(f"Creating folder '{folder_name}' in parent ID: {full_parent_id}")
        temp_client_id: str = f"FOLDER::UNKNOWN_ZONE::TempId-{uuid.uuid4()}"
        
        request_data = {
            "destinationDrivewsId": full_parent_id,
            "folders": [{
                "clientId": temp_client_id,
                "name": folder_name,
            }],
        }

        try:
            response = self._session.post(
                f"{self._service_root}/createFolders",
                params=self._params,
                json=request_data,
            )
            self._raise_if_error(response)
            return response.json()
        except Exception as e:
            raise Exception(f"Failed to create folder '{folder_name}'") from e


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    
    try:
        from .client import OrchardiCloudClient
    except ImportError:
        try:
            import sys
            sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
            from src.icloud_client.client import OrchardiCloudClient
        except ImportError:
            print("Error: Could not import OrchardiCloudClient. Ensure project structure is correct.")
            sys.exit(1)

    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    def main():
        parser = argparse.ArgumentParser(description="Traverse iCloud Drive filesystem.")
        parser.add_argument("--apple-id", required=True, help="Your Apple ID.")
        parser.add_argument("--cookie-dir", 
                            default=str(Path(__file__).parent.parent.parent / ".icloud_session"),
                            help="Directory to store iCloud session cookies.")
        args = parser.parse_args()

        client = OrchardiCloudClient(
            apple_id=args.apple_id,
            password=None,
            cookie_directory=args.cookie_dir
        )

        print(f"Authenticating {args.apple_id}...")
        client.authenticate()
        
        if not client.authenticated:
            LOGGER.error("Authentication failed.")
            return

        drive_service_root = client.get_webservice_url("drivews")
        drive_document_root = client.get_webservice_url("docws")
        
        icloud_drive = iCloudDrive(
            session=client.session,
            service_root=drive_service_root,
            document_root=drive_document_root,
            params=client._pyicloud_service.params
        )

        current_folder_id = CLOUD_DOCS_ZONE_ID_ROOT
        current_path = "/"
        
        print("\nCommands: ls, ls_trash, cd <dir>, inspect <name>, download <file> <path>, download_dir <folder> <path>, upload <path>, mkdir <name>, rename <old> <new>, move <name> <dest>, copy <name> <dest>, delete <name>, rmdir <name>, recover <name>, purge <name>, exit")

        while True:
            try:
                print(f"\n{current_path}$ ", end="")
                cmd_input = input().strip().split(" ")
                cmd = cmd_input[0].lower()
                args = cmd_input[1:]

                if cmd == "exit": break
                
                elif cmd == "ls":
                    contents = icloud_drive.list_directory(current_folder_id)
                    print(f"{'Type':<8} {'Name':<30} {'Size':>10}")
                    for item in contents:
                        size = item.get("size", "") if item.get("type") == "FILE" else "-"
                        print(f"{item.get('type'):<8} {item.get('name'):<30} {size:>10}")

                elif cmd == "ls_trash":
                    contents = icloud_drive.list_trash()
                    print(f"{'Type':<8} {'Name':<30} {'Size':>10}")
                    for item in contents:
                        size = item.get("size", "") if item.get("type") == "FILE" else "-"
                        print(f"{item.get('type'):<8} {item.get('name'):<30} {size:>10}")

                elif cmd == "cd":
                    if not args: continue
                    target = args[0]
                    if target == "..":
                        print("Parent navigation not implemented in CLI test.")
                        continue
                    
                    contents = icloud_drive.list_directory(current_folder_id)
                    found = next((i for i in contents if i.get("name") == target and i.get("type") == "FOLDER"), None)
                    if found:
                        current_folder_id = found["drivewsid"]
                        current_path = os.path.join(current_path, target)
                    else:
                        print("Folder not found.")

                elif cmd == "upload":
                    if not args: print("Usage: upload <local_path>"); continue
                    icloud_drive.upload_file(args[0], current_folder_id)
                    print("Upload successful.")

                elif cmd == "mkdir":
                    if not args: print("Usage: mkdir <name>"); continue
                    icloud_drive.create_folder(current_folder_id, args[0])
                    print("Folder created.")

                elif cmd == "download":
                    if not args: print("Usage: download <filename> [local_path]"); continue
                    name = args[0]
                    contents = icloud_drive.list_directory(current_folder_id)
                    found = next((i for i in contents if i.get("name") == name and i.get("type") == "FILE"), None)
                    if found:
                        local_p = args[1] if len(args) > 1 else None
                        file_id = found.get("docwsid", found.get("drivewsid"))
                        icloud_drive.download_file(file_id, local_path=local_p)
                        print("Downloaded.")
                    else:
                        print("File not found.")

                elif cmd == "download_dir":
                    if len(args) < 2: print("Usage: download_dir <foldername> <local_path>"); continue
                    name, local_p = args[0], args[1]
                    contents = icloud_drive.list_directory(current_folder_id)
                    found = next((i for i in contents if i.get("name") == name and i.get("type") == "FOLDER"), None)
                    if found:
                        icloud_drive.download_directory(found["drivewsid"], local_path=local_p)
                        print("Directory Downloaded.")
                    else:
                        print("Folder not found.")

                elif cmd == "inspect":
                    if not args: print("Usage: inspect <name>"); continue
                    name = args[0]
                    contents = icloud_drive.list_directory(current_folder_id)
                    found = next((i for i in contents if i.get("name") == name), None)
                    if found:
                        print(json.dumps(found, indent=2))
                        print("\n--- Detailed Metadata Check ---")
                        # Pass parent_id so metadata fetch can succeed for files
                        meta_id = found.get("drivewsid", found.get("docwsid"))
                        parent_id = current_folder_id
                        meta = icloud_drive.get_item_metadata(meta_id)
                        print(json.dumps(meta, indent=2))
                    else:
                        print("Item not found.")

                elif cmd in ["delete", "rmdir"]:
                    if not args: print(f"Usage: {cmd} <name>"); continue
                    name = args[0]
                    contents = icloud_drive.list_directory(current_folder_id)
                    found = next((i for i in contents if i.get("name") == name), None)
                    if found:
                        icloud_drive.delete_item(found["drivewsid"], found["etag"])
                        print("Deleted (Moved to Trash).")
                    else:
                        print("Item not found.")

                elif cmd == "recover":
                    if not args: print("Usage: recover <name_in_trash>"); continue
                    name = args[0]
                    contents = icloud_drive.list_trash()
                    found = next((i for i in contents if i.get("name") == name), None)
                    if found:
                        icloud_drive.recover_item(found["drivewsid"], found["etag"])
                        print("Recovered.")
                    else:
                        print("Item not found in Trash.")

                elif cmd == "purge":
                    if not args: print("Usage: purge <name_in_trash>"); continue
                    name = args[0]
                    contents = icloud_drive.list_trash()
                    found = next((i for i in contents if i.get("name") == name), None)
                    if found:
                        icloud_drive.delete_item(found["drivewsid"], found["etag"])
                        print("Permanently deleted.")
                    else:
                        print("Item not found in Trash.")

                elif cmd in ["move", "mv"]:
                    if len(args) < 2: print("Usage: move <name> <dest_folder_name>"); continue
                    name, dest_name = args[0], args[1]
                    contents = icloud_drive.list_directory(current_folder_id)
                    
                    item = next((i for i in contents if i.get("name") == name), None)
                    dest = next((i for i in contents if i.get("name") == dest_name and i.get("type") == "FOLDER"), None)
                    
                    if item and dest:
                        icloud_drive.move_item(item["drivewsid"], item["etag"], dest["drivewsid"])
                        print("Moved.")
                    else:
                        print("Source item or destination folder not found.")

                elif cmd == "copy":
                    if len(args) < 2: print("Usage: copy <name> <dest_folder_name>"); continue
                    name, dest_name = args[0], args[1]
                    contents = icloud_drive.list_directory(current_folder_id)
                    
                    item = next((i for i in contents if i.get("name") == name), None)
                    dest = next((i for i in contents if i.get("name") == dest_name and i.get("type") == "FOLDER"), None)
                    
                    if item and dest:
                        icloud_drive.copy_item(item["drivewsid"], item["etag"], dest["drivewsid"])
                        print("Copied.")
                    else:
                        print("Source item or destination folder not found.")
                        
            except Exception as e:
                print(f"Error: {e}")

    main()