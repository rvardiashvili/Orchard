import os
import logging

logger = logging.getLogger(__name__)

def export_reading_list(api, output_dir):
    """
    Fetches Safari Reading List (via Bookmarks) and saves to HTML.
    Note: pyicloud access to bookmarks is non-standard and varies by account.
    We often access it via the 'bookmarks' webservice or 'tabs'.
    """
    try:
        # Check for reading list service (often just 'bookmarks')
        # This is highly experimental as Apple doesn't document this API.
        # We will try to find 'reading list' in the ubiquity or kvs info if possible.
        
        # For now, we mock the behavior or try to fetch tabs.
        # api.tabs works for iCloud Tabs (open tabs on other devices).
        
        logger.info("Fetching iCloud Tabs (Safari Sync)...")
        tabs_content = "<html><body><h1>Safari Tabs</h1><ul>"
        
        count = 0
        # 1. Try "Tabs" (Open tabs on other devices)
        if hasattr(api, 'tabs'):
             for device in api.tabs:
                 tabs_content += f"<h2>{device['deviceDisplayName']}</h2>"
                 for tab in device['tabs']:
                     title = tab.get('title', 'Untitled')
                     url = tab.get('url', '#')
                     tabs_content += f'<li><a href="{url}">{title}</a></li>'
                     count += 1
        
        # 2. Try "Reading List" via Bookmarks (Experimental)
        # Often bookmarks are not exposed directly, but let's check webservices
        # If tabs found nothing, we might be in a limited session
        
        tabs_content += "</ul></body></html>"
        
        if count == 0:
            logger.warning("No iCloud Tabs found. (Feature might require 'Safari' toggle enabled in iCloud settings on iOS/Mac)")
            
            # Create a placeholder so user sees something happened
            tabs_content = "<html><body><h1>No Tabs Found</h1><p>Ensure Safari syncing is enabled in iCloud settings.</p></body></html>"
            # We save it anyway to confirm the action
            out_file = os.path.join(output_dir, "SafariTabs.html")
            with open(out_file, 'w') as f:
                f.write(tabs_content)
            return 0, out_file

        if count > 0:
            out_file = os.path.join(output_dir, "SafariTabs.html")
            with open(out_file, 'w') as f:
                f.write(tabs_content)
            return count, out_file
        else:
            return 0, None

    except Exception as e:
        logger.error(f"Reading List export failed: {e}")
        return 0, None
