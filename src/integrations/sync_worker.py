import os
import json
import logging
import time

logger = logging.getLogger(__name__)

def sync_all_services(api, cache_dir):
    """
    Main entry point to sync auxiliary services (Contacts, Reminders, Notes).
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

    logger.info("Service Sync Cycle Complete.")

def sync_contacts(api, cache_dir):
    if not hasattr(api, 'contacts'):
        return

    logger.info("Exporting Contacts...")
    try:
        contacts = api.contacts.all
        vcf_path = os.path.join(cache_dir, "contacts.vcf")
        
        with open(vcf_path, 'w', encoding='utf-8') as f:
            for c in contacts:
                # Basic VCF 3.0 construction
                first = c.get('firstName', '')
                last = c.get('lastName', '')
                full_name = f"{first} {last}".strip()
                if not full_name:
                    # Try company name
                    full_name = c.get('companyName', 'Unknown')
                    
                f.write("BEGIN:VCARD\nVERSION:3.0\n")
                f.write(f"FN:{full_name}\n")
                f.write(f"N:{last};{first};;;\n")
                
                # Phones
                for p in c.get('phones', []):
                    label = p.get('label', 'CELL').upper()
                    number = p.get('field', '')
                    f.write(f"TEL;TYPE={label}:{number}\n")
                
                # Emails
                for e in c.get('emailAddresses', []):
                    label = e.get('label', 'HOME').upper()
                    email = e.get('field', '')
                    f.write(f"EMAIL;TYPE={label}:{email}\n")
                    
                f.write("END:VCARD\n")
        
        logger.info(f"Contacts saved to {vcf_path}")
    except Exception as e:
        logger.error(f"Error fetching contacts: {e}")

from src.integrations.apple_reminders import AppleReminders
from src.integrations.apple_notes import AppleNotes

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
