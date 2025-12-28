import logging
import json
import time
import base64
import zlib
import re
import datetime
import requests
from src.auth import AuthManager

logger = logging.getLogger(__name__)

class AppleReminders:
    def __init__(self, api_client=None):
        self.api = api_client
        if not self.api:
            mgr = AuthManager(username="vardiashvilirati33@gmail.com")
            self.api = mgr.get_service()
            
        self._cache = {} 
        self._last_refresh = 0
        self.CACHE_TTL = 60 

    def refresh_if_needed(self):
        if time.time() - self._last_refresh > self.CACHE_TTL:
            self.refresh()

    def refresh(self):
        logger.info("Refreshing Reminders via CloudKit...")
        try:
            data = self._fetch_from_cloudkit()
            if data:
                self._cache = data
                self._last_refresh = time.time()
                logger.info(f"Refreshed {len(self._cache)} reminder lists.")
        except Exception as e:
            logger.error(f"Failed to refresh reminders: {e}")

    def list_folders(self):
        self.refresh_if_needed()
        return list(self._cache.keys())

    def get_list_content(self, list_name):
        self.refresh_if_needed()
        return self._cache.get(list_name, [])

    def get_list_as_markdown(self, list_name):
        tasks = self.get_list_content(list_name)
        if not tasks:
            return ""
        
        lines = [f"# {list_name}", ""]
        
        # Split into Pending and Completed for better UX
        pending = [t for t in tasks if not t['completed']]
        completed = [t for t in tasks if t['completed']]
        
        for task in pending:
            lines.append(self._format_task_md(task))
            
        if completed:
            lines.append("\n## Completed")
            for task in completed:
                lines.append(self._format_task_md(task))
        
        return "\n".join(lines)

    def _format_task_md(self, task):
        checkbox = "[x]" if task['completed'] else "[ ]"
        line = f"- {checkbox} {task['title']}"
        
        meta = []
        if task.get('priority'):
            meta.append(f"Priority: {task['priority']}")
        if task.get('due_date'):
            # Timestamp -> Readable
            try:
                dt = datetime.datetime.fromtimestamp(task['due_date'] / 1000.0)
                meta.append(f"Due: {dt.strftime('%Y-%m-%d %H:%M')}")
            except: pass
        
        if meta:
            line += f" ({', '.join(meta)})"
            
        if task.get('notes'):
            # Indent notes
            for note_line in task['notes'].split('\n'):
                line += f"\n    > {note_line}"
                
        return line

    def _extract_text_from_document(self, b64_val):
        if not b64_val: return None
        try:
            compressed = base64.b64decode(b64_val)
            decompressed = None
            try: decompressed = zlib.decompress(compressed)
            except: 
                try: decompressed = zlib.decompress(compressed, -15)
                except: 
                    try: decompressed = zlib.decompress(compressed, 16+zlib.MAX_WBITS)
                    except: decompressed = compressed # Fallback

            # Heuristic Extraction
            candidates = []
            current_bytes = bytearray()
            for b in decompressed:
                if (32 <= b <= 126) or b > 127: 
                    current_bytes.append(b)
                else:
                    if len(current_bytes) > 0:
                        try:
                            s = current_bytes.decode('utf-8')
                            if len(s.strip()) > 1: candidates.append(s)
                        except: pass
                    current_bytes = bytearray()
            
            # Flush last
            if len(current_bytes) > 0:
                try:
                    s = current_bytes.decode('utf-8')
                    if len(s.strip()) > 1: candidates.append(s)
                except: pass

            if candidates:
                return max(candidates, key=len)
        except: 
            pass
        return None

    def _fetch_from_cloudkit(self):
        if 'ckdatabasews' not in self.api.data['webservices']:
            return {}

        ck_base_url = self.api.data['webservices']['ckdatabasews']['url']
        container = "com.apple.reminders"
        params = {
            'ckjs': '2.0',
            'clientBuildNumber': self.api.params['clientBuildNumber'],
            'dsid': self.api.params['dsid'],
        }

        # 1. Get Zone
        zones_url = f"{ck_base_url}/database/1/{container}/production/private/zones/list"
        reminders_zone = None
        try:
            res = self.api.session.post(zones_url, params=params, json={})
            if res.status_code == 200:
                data = res.json()
                for z in data.get('zones', []):
                    if z.get('zoneID', {}).get('zoneName') == 'Reminders':
                        reminders_zone = z['zoneID']
                        break
        except: return {}

        if not reminders_zone: return {}

        clean_zone = {
            'zoneName': reminders_zone['zoneName'],
            'ownerRecordName': reminders_zone['ownerRecordName']
        }

        # 2. Sync Records
        sync_url = f"{ck_base_url}/database/1/{container}/production/private/changes/zone"
        payload = {"zones": [{"zoneID": clean_zone}], "resultsLimit": 300}

        try:
            res = requests.post(
                sync_url, params=params, json=payload,
                headers=self.api.session.headers, cookies=self.api.session.cookies
            )
            if res.status_code != 200: return {}
            
            data = res.json()
            records = []
            for z in data.get('zones', []):
                records.extend(z.get('records', []))
                
        except: return {}

        # 3. Process
        lists = {}
        reminders = []
        
        for r in records:
            rtype = r.get('recordType')
            fields = r.get('fields', {})
            
            if rtype == 'List':
                title_obj = fields.get('Title', {}) or fields.get('Name', {})
                lists[r.get('recordName')] = title_obj.get('value', 'Untitled List')
                
            elif rtype == 'Reminder':
                # Title
                text = "Untitled Reminder"
                if 'TitleDocument' in fields:
                    extracted = self._extract_text_from_document(fields['TitleDocument'].get('value'))
                    if extracted: text = extracted
                
                if text == "Untitled Reminder":
                     text = fields.get('Title', {}).get('value') or "Untitled Reminder"

                # Notes
                notes = None
                if 'NotesDocument' in fields:
                    notes = self._extract_text_from_document(fields['NotesDocument'].get('value'))

                list_ref = fields.get('List', {}).get('value', {}).get('recordName')
                
                reminders.append({
                    'title': text,
                    'list_id': list_ref,
                    'completed': (fields.get('Completed', {}).get('value', 0) == 1),
                    'priority': fields.get('Priority', {}).get('value'),
                    'due_date': fields.get('DueDate', {}).get('value'),
                    'notes': notes,
                    'creation_date': fields.get('CreationDate', {}).get('value')
                })

        # 4. Group
        result = {}
        for l_name in lists.values():
            result[l_name] = []
            
        for rem in reminders:
            l_id = rem['list_id']
            if l_id in lists:
                result[lists[l_id]].append(rem)
            else:
                if 'Orphans' not in result: result['Orphans'] = []
                result['Orphans'].append(rem)
                
        return result
