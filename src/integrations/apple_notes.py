import logging
import json
import time
import base64
import zlib
import re
import requests
from src.auth import AuthManager

logger = logging.getLogger(__name__)

class AppleNotes:
    def __init__(self, api_client=None):
        self.api = api_client
        if not self.api:
            mgr = AuthManager(username="vardiashvilirati33@gmail.com")
            self.api = mgr.get_service()
            
        self._cache = [] # List of Note dicts
        self._last_refresh = 0
        self.CACHE_TTL = 60 

    def refresh_if_needed(self):
        if time.time() - self._last_refresh > self.CACHE_TTL:
            self.refresh()

    def refresh(self):
        logger.info("Refreshing Notes via CloudKit...")
        try:
            notes = self._fetch_from_cloudkit()
            if notes:
                self._cache = notes
                self._last_refresh = time.time()
                logger.info(f"Refreshed {len(self._cache)} notes.")
        except Exception as e:
            logger.error(f"Failed to refresh notes: {e}")

    def list_notes(self):
        self.refresh_if_needed()
        return self._cache

    def get_note(self, title):
        self.refresh_if_needed()
        for n in self._cache:
            if n['title'] == title:
                return n
        return None

    def _extract_text(self, b64_val, is_compressed_likely=False):
        if not b64_val: return ""
        try:
            data = base64.b64decode(b64_val)
            
            # 1. Try Decompression
            decompressed = None
            if is_compressed_likely:
                try: decompressed = zlib.decompress(data, 16+zlib.MAX_WBITS)
                except: 
                    try: decompressed = zlib.decompress(data)
                    except: pass
            
            if decompressed:
                data = decompressed
            
            # 2. Extract Text (Heuristic)
            # Try direct UTF-8 first
            try:
                return data.decode('utf-8')
            except:
                pass

            # Fallback: Extract strings from binary
            candidates = []
            current_bytes = bytearray()
            
            for b in data:
                # Allow: Printable (32-126), Newline (10), Tab (9), and UTF-8 continuation bytes (>127)
                if (32 <= b <= 126) or b > 127 or b in (10, 9): 
                    current_bytes.append(b)
                else:
                    if len(current_bytes) > 0:
                        self._process_candidate(current_bytes, candidates)
                    current_bytes = bytearray()
            
            # Flush last
            if len(current_bytes) > 0:
                self._process_candidate(current_bytes, candidates)

            # Join candidates
            # Heuristic: If it ends with newline, assume paragraph break.
            # Otherwise assume word fragment? 
            # Apple Notes usually puts full paragraphs in protobuf strings.
            return "\n".join(candidates)
            
        except Exception as e:
            logger.debug(f"Extraction error: {e}")
            return ""

    def _process_candidate(self, bytes_obj, candidates_list):
        try:
            s = bytes_obj.decode('utf-8')
            clean_s = s.strip()
            
            # Filter 1: Absolute Minimum Length
            if len(clean_s) < 2: return
            
            # Calculate alphanumeric density
            alnum = sum(c.isalnum() or c.isspace() for c in clean_s)
            total = len(clean_s)
            ratio = alnum / total if total > 0 else 0
            
            # Filter 2: Adaptive Thresholds
            if len(clean_s) < 5:
                # Short strings must be mostly clean text (e.g. "The", "Cat")
                # Reject "!(A" (ratio ~0.33) or "U(n" (ratio ~0.66)
                if ratio < 0.8: return
            else:
                # Longer strings can have more punctuation (sentences)
                if ratio < 0.4: return
            
            # Filter 3: Common proto junk keywords (if isolated)
            if clean_s.lower() in ['note', 'document', 'body', 'text', 'html', 'div']:
                return
                
            candidates_list.append(s)
        except:
            pass

    def _fetch_from_cloudkit(self):
        if 'ckdatabasews' not in self.api.data['webservices']:
            return []

        ck_base_url = self.api.data['webservices']['ckdatabasews']['url']
        container = "com.apple.notes"
        params = {
            'ckjs': '2.0',
            'clientBuildNumber': self.api.params['clientBuildNumber'],
            'dsid': self.api.params['dsid'],
        }

        # 1. Get Zone
        zones_url = f"{ck_base_url}/database/1/{container}/production/private/zones/list"
        notes_zone = None
        try:
            res = self.api.session.post(zones_url, params=params, json={})
            if res.status_code == 200:
                data = res.json()
                for z in data.get('zones', []):
                    if z.get('zoneID', {}).get('zoneName') == 'Notes':
                        notes_zone = z['zoneID']
                        break
        except: return []

        if not notes_zone: return []

        clean_zone = {
            'zoneName': notes_zone['zoneName'],
            'ownerRecordName': notes_zone['ownerRecordName']
        }

        # 2. Sync Records
        sync_url = f"{ck_base_url}/database/1/{container}/production/private/changes/zone"
        payload = {"zones": [{"zoneID": clean_zone}], "resultsLimit": 150}

        try:
            res = requests.post(
                sync_url, params=params, json=payload,
                headers=self.api.session.headers, cookies=self.api.session.cookies
            )
            if res.status_code != 200: return []
            
            data = res.json()
            records = []
            for z in data.get('zones', []):
                records.extend(z.get('records', []))
                
        except: return []

        # 3. Process Notes
        notes = []
        attachments_map = {} # NoteID -> Count
        
        # First Pass: Count attachments
        for r in records:
            if r.get('recordType') in ('Attachment', 'Media'):
                # Try to find parent pointer
                # Usually in 'Parent' or 'Note' field
                fields = r.get('fields', {})
                parent = fields.get('Parent', {}).get('value', {}).get('recordName')
                if not parent:
                    parent = fields.get('Note', {}).get('value', {}).get('recordName')
                
                if parent:
                    attachments_map[parent] = attachments_map.get(parent, 0) + 1

        # Second Pass: Build Notes
        for r in records:
            if r.get('recordType') == 'Note':
                fields = r.get('fields', {})
                
                # Title
                title = "Untitled Note"
                if 'TitleEncrypted' in fields:
                    t = self._extract_text(fields['TitleEncrypted'].get('value'), is_compressed_likely=False)
                    if t: title = t.replace('\n', ' ').strip()
                
                # Body
                body = ""
                if 'TextDataEncrypted' in fields:
                    body = self._extract_text(fields['TextDataEncrypted'].get('value'), is_compressed_likely=True)
                elif 'SnippetEncrypted' in fields:
                    body = self._extract_text(fields['SnippetEncrypted'].get('value'), is_compressed_likely=False)

                # Attachments Footer
                rec_name = r.get('recordName')
                att_count = attachments_map.get(rec_name, 0)
                if att_count > 0:
                    body += f"\n\n[System: {att_count} Attachment(s) present but not displayed]"

                folder_ref = fields.get('Folder', {}).get('value', {}).get('recordName')
                
                notes.append({
                    'id': rec_name,
                    'title': title,
                    'body': body,
                    'folder_id': folder_ref,
                    'created': fields.get('CreationDate', {}).get('value'),
                    'modified': fields.get('ModificationDate', {}).get('value')
                })
        
        return notes

