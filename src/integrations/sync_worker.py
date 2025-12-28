import os
import json
import logging
import time

logger = logging.getLogger(__name__)

from src.integrations.apple_reminders import AppleReminders
from src.integrations.apple_notes import AppleNotes
from src.integrations.apple_contacts import AppleContacts
from src.integrations.apple_calendar import AppleCalendar

def sync_all_services(api, cache_dir):
    """
    Main entry point to sync auxiliary services (Contacts, Reminders, Notes, Calendar).
    """
    logger.info("Starting Service Sync...")
    
    try:
        sync_contacts(api, cache_dir)
    except Exception as e:
        logger.error(f"Contacts sync failed: {e}")

    try:
        sync_reminders(api, cache_dir)
    except Exception as e:
        logger.error(f"Reminders sync failed: {e}")

    try:
        sync_notes(api, cache_dir)
    except Exception as e:
        logger.error(f"Notes sync failed: {e}")

    try:
        sync_calendar(api, cache_dir)
    except Exception as e:
        logger.error(f"Calendar sync failed: {e}")

    logger.info("Service Sync Cycle Complete.")

def sync_contacts(api, cache_dir):
    logger.info("Syncing Contacts...")
    try:
        svc = AppleContacts(api)
        contacts = svc.fetch_all()
        vcf_path = os.path.join(cache_dir, "contacts.vcf")
        
        with open(vcf_path, 'w', encoding='utf-8') as f:
            for c in contacts:
                try:
                    vcard_str = svc.export_vcard(c)
                    f.write(vcard_str)
                    f.write("\n")
                except Exception as e:
                    logger.warning(f"Failed to export contact: {e}")
        
        logger.info(f"Contacts saved to {vcf_path}")
    except Exception as e:
        logger.error(f"Error fetching contacts: {e}")

def sync_calendar(api, cache_dir):
    logger.info("Syncing Calendar...")
    try:
        svc = AppleCalendar(api)
        events = svc.fetch_events(days_back=30, days_forward=90)
        
        # 1. ICS Export
        ics_path = os.path.join(cache_dir, "Calendar.ics")
        ics_data = svc.export_ics(events)
        with open(ics_path, 'wb') as f: # ICS data is bytes (utf-8 encoded) or string? icalendar to_ical returns bytes
             # export_ics in my implementation returned decode('utf-8'), so it's string.
             # Wait, let's check AppleCalendar.export_ics return type.
             # It returns cal.to_ical().decode('utf-8'). So it is string.
             f.write(ics_data.encode('utf-8'))
        
        # 2. Markdown Export
        md_path = os.path.join(cache_dir, "Calendar.md")
        md_data = svc.export_markdown(events)
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(md_data)
            f.write("\n\n*Synced via Orchard*")
            
        logger.info(f"Calendar saved to {ics_path}")
    except Exception as e:
        logger.error(f"Error fetching calendar: {e}")

def sync_reminders(api, cache_dir):
    # Use our custom CloudKit implementation
    logger.info("Syncing Reminders (via CloudKit)...")
    
    try:
        reminders_svc = AppleReminders(api)
        reminders_svc.refresh() # Force fetch
        
        # Get raw cache data: {'List Name': [tasks...]}
        reminders_data = reminders_svc._cache
        
        # 1. Save raw JSON
        json_path = os.path.join(cache_dir, "reminders.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(reminders_data, f, indent=2, default=str)
            
        # 2. Save Markdown (Readable)
        md_path = os.path.join(cache_dir, "Reminders.md")
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write("# Reminders\n\n")
            
            if isinstance(reminders_data, dict):
                for title, tasks in reminders_data.items():
                    f.write(f"## {title}\n")
                    if isinstance(tasks, list):
                        for task in tasks:
                            t_title = task.get('title', 'Untitled')
                            completed = task.get('completed', False)
                            chk = "[x]" if completed else "[ ]"
                            f.write(f"- {chk} {t_title}\n")
                    f.write("\n")
            
            f.write("\n\n*Synced via Orchard*")
                
        logger.info(f"Reminders saved to {json_path}")
    except Exception as e:
        logger.error(f"Error fetching reminders: {e}")

def sync_notes(api, cache_dir):
    logger.info("Syncing Notes (via CloudKit)...")
    notes_root = os.path.join(cache_dir, "Notes")
    os.makedirs(notes_root, exist_ok=True)
    
    try:
        notes_svc = AppleNotes(api)
        notes_svc.refresh()
        notes = notes_svc.list_notes()
        
        count = 0
        for note in notes:
            title = note.get('title', 'Untitled')
            body = note.get('body', '')
            
            # Sanitize filename
            safe_title = "".join([c for c in title if c.isalnum() or c in (' ', '-', '_')]).strip()
            if not safe_title: safe_title = f"Note_{note['id']}"
            
            note_path = os.path.join(notes_root, f"{safe_title}.txt")
            
            with open(note_path, 'w', encoding='utf-8') as f:
                f.write(f"Title: {title}\n")
                f.write(f"Date: {note.get('created')}\n")
                f.write("-" * 20 + "\n\n")
                f.write(body)
            count += 1
            
        logger.info(f"Synced {count} notes to {notes_root}")
    except Exception as e:
        logger.error(f"Error syncing notes: {e}")
