import os
import logging
import caldav
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

def sync_calendar(api, start_date=None, end_date=None):
    """
    Syncs iCloud Calendar events to a local .ics file or list.
    Since pyicloud doesn't support CalDAV directly, we usually use the 
    'calendar' webservice URL which is a CalDAV endpoint.
    """
    # NOTE: iCloud CalDAV requires an App-Specific Password, not the session token.
    # This is a limitation of Apple's auth. 
    # WE CANNOT use the existing 'api' session for CalDAV easily.
    # We would need to ask the user for an App-Specific Password or use the 
    # internal JSON API (private) which 'pyicloud' exposes partially. 
    
    # Strategy: Use pyicloud's internal calendar service wrapper if available
    try:
        if hasattr(api, 'calendar'):
            events = api.calendar.events(start_date, end_date)
            return events
    except Exception as e:
        logger.error(f"Calendar sync error: {e}")
    return []

def sync_reminders(api):
    try:
        if hasattr(api, 'reminders'):
            return api.reminders.lists
    except Exception as e:
        logger.error(f"Reminders sync error: {e}")
    return []

def export_contacts(api, output_dir):
    try:
        if hasattr(api, 'contacts'):
            # api.contacts.all is a property (list), not a method
            contacts = api.contacts.all
            # Convert to VCF
            vcf_path = os.path.join(output_dir, "contacts.vcf")
            with open(vcf_path, 'w') as f:
                for c in contacts:
                    # Very basic VCF construction
                    name = c.get('firstName', '') + ' ' + c.get('lastName', '')
                    phones = c.get('phones', [])
                    f.write("BEGIN:VCARD\nVERSION:3.0\n")
                    f.write(f"FN:{name}\n")
                    for p in phones:
                        f.write(f"TEL:{p.get('field', '')}\n")
                    f.write("END:VCARD\n")
            return vcf_path
    except Exception as e:
        logger.error(f"Contacts export error: {e}")
        return None

def sync_notes(api, cache_dir):
    """
    Syncs iCloud Notes to local text/html files.
    """
    notes_dir = os.path.join(cache_dir, "Notes")
    if not os.path.exists(notes_dir):
        os.makedirs(notes_dir)
        
    logger.info("Syncing Notes...")
    count = 0
    
    try:
        # Notes are often hidden in specialized Drive folders or via a separate service
        # Strategy: Look for "Notes" folder in Drive
        notes_node = None
        for name in api.drive.dir():
            if "Notes" in name:
                notes_node = api.drive[name]
                break
        
        if notes_node:
            for note_name in notes_node.dir():
                try:
                    # Download note file
                    # Usually .txt or proprietary format
                    local_path = os.path.join(notes_dir, note_name)
                    with notes_node[note_name].open(stream=True) as response:
                        with open(local_path, 'wb') as f:
                            for chunk in response.iter_content(chunk_size=8192):
                                f.write(chunk)
                    count += 1
                except Exception:
                    pass
        else:
            # Fallback: Check if 'notes' service is exposed directly
            if hasattr(api, 'notes'):
                for note in api.notes.all():
                    # proprietary object?
                    pass
            logger.info("No standard Notes folder found in Drive root.")

    except Exception as e:
        logger.error(f"Notes sync error: {e}")
        
    return count, notes_dir
