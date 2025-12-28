import os
import logging
from flask import Flask, request, jsonify, render_template
import threading
from src.device_status import get_devices, play_sound
import src.metadata_crawler # Import the module to access the global crawler instance

logger = logging.getLogger(__name__)

app = Flask(__name__)
# ... (existing code) ...

@app.route('/api/v1/search', methods=['POST'])
def search_docs():
    """
    Search Files (Metadata Map Only - Filenames)
    """
    data = request.json
    query = data.get('query', '')
    if not query:
        return jsonify({"results": []})
        
    results = []
    
    # Search the Cloud Map (Fast, Filenames only, covers ALL files)
    if src.metadata_crawler.crawler:
        map_results = src.metadata_crawler.crawler.search(query)
        results.extend(map_results)
    else:
        # Fallback if crawler not ready
        return jsonify({"results": [], "message": "Crawler initializing..."})
        
    return jsonify({"results": results, "count": len(results)})

@app.route('/api/v1/sync_all', methods=['POST'])
def trigger_full_sync():
    """
    Downloads ALL files from iCloud to Local Cache so they can be indexed.
    """
    if not API_CLIENT:
        return jsonify({"error": "Offline"}), 503
        
    local_cache = os.path.expanduser("~/.cache/icloud_sync")
    
    def download_worker():
        logger.info("Starting Background Full Sync...")
        try:
            # Recursive downloader
            def _download(node, rel_path=""):
                for name in node.dir():
                    child = node[name]
                    path = os.path.join(rel_path, name)
                    
                    if child.type == 'file':
                        # Download if missing/changed
                        local_f = os.path.join(local_cache, path)
                        if not os.path.exists(local_f) or os.path.getsize(local_f) != child.size:
                            logger.info(f"Downloading {path}...")
                            os.makedirs(os.path.dirname(local_f), exist_ok=True)
                            with child.open(stream=True) as response:
                                with open(local_f, 'wb') as f:
                                    for chunk in response.iter_content(chunk_size=8192):
                                        f.write(chunk)
                    elif child.type == 'folder':
                        _download(child, path)
                        
            _download(API_CLIENT.drive)
            logger.info("Full Sync Complete.")
            
            # Re-index after sync
            # global brain
            # if brain: 
            #    brain.index_files()
                
        except Exception as e:
            logger.error(f"Full Sync Failed: {e}")

    threading.Thread(target=download_worker, daemon=True).start()
    return jsonify({"status": "started", "message": "Full download started in background. Check logs."})

@app.route('/')
def dashboard():
    return render_template('dashboard.html')

@app.route('/api/v1/status', methods=['GET'])
def status():
    user = "Unknown"
    devices = []
    
    if API_CLIENT:
        try:
            # Debugging: Log available attributes to help identify the correct one
            # logger.info(f"API Client Dir: {dir(API_CLIENT)}")
            
            user = "Unknown"
            
            # Strategy: data['dsInfo']['fullName'] (Found via debug)
            if hasattr(API_CLIENT, 'data') and isinstance(API_CLIENT.data, dict):
                ds_info = API_CLIENT.data.get('dsInfo', {})
                user = ds_info.get('fullName')

            # Fallback strategies
            if not user or user == "Unknown":
                if hasattr(API_CLIENT, 'user') and isinstance(API_CLIENT.user, dict):
                    user = API_CLIENT.user.get('fullName')
            
            if not user or user == "Unknown":
                user = getattr(API_CLIENT, 'username', None)
                
            if not user:
                user = "Apple User (Name Hidden)"

            # Fetch devices
            devices = get_devices(API_CLIENT) 
        except Exception as e:
            logger.error(f"Error fetching dashboard info: {repr(e)}", exc_info=True)
            user = f"Error: {repr(e)}"

    return jsonify({
        "status": "running",
        "service": "Orchard-Bridge",
        "sync_root": SYNC_ROOT,
        "user": user,
        "devices": devices
    })

@app.route('/api/v1/ping', methods=['POST'])
def ping_device():
    data = request.json
    dev_id = data.get('id')
    if API_CLIENT and dev_id:
        play_sound(API_CLIENT, dev_id)
        return jsonify({"status": "sent"})
    return jsonify({"error": "not connected"}), 400

@app.route('/api/v1/clipboard', methods=['POST'])
def update_clipboard():
    """
    Receives text from iOS Shortcut -> Updates local clipboard
    """
    data = request.json
    text = data.get('text', '')
    
    if not text:
        return jsonify({"error": "No text provided"}), 400

    local_cache_dir = os.path.expanduser("~/.cache/icloud_sync")
    if not os.path.exists(local_cache_dir):
        os.makedirs(local_cache_dir, exist_ok=True)
        
    clipboard_file = os.path.join(local_cache_dir, ".clipboard")
    try:
        with open(clipboard_file, 'w') as f:
            f.write(text)
        
        # Try direct injection if xclip is available
        # os.system(f"echo '{text}' | xclip -selection clipboard") 
        
        logger.info(f"Clipboard updated via API: {text[:20]}...")
        return jsonify({"status": "success", "message": "Clipboard updated"})
    except Exception as e:
        logger.error(f"Clipboard error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/v1/open', methods=['POST'])
def open_url():
    """
    Receives URL from iOS -> Opens in default browser
    """
    data = request.json
    url = data.get('url', '')
    
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    # Write to handoff file in the local cache, not the FUSE mount
    # The FUSE mount is read-only for safety, so we can't write to ~/iCloud/LinuxSync directly via the fs
    # Instead, we write to the local cache dir which the user might sync later, or we just keep it local.
    
    # We need to find the cache dir. Currently SYNC_ROOT is passed.
    # We will assume a .local folder alongside the sync root or use a standard path.
    local_handoff_dir = os.path.expanduser("~/.cache/icloud_sync/LinuxSync")
    handoff_file = os.path.join(local_handoff_dir, "handoff.log")
    
    try:
        if not os.path.exists(os.path.dirname(handoff_file)):
            os.makedirs(os.path.dirname(handoff_file), exist_ok=True)
            
        with open(handoff_file, 'a') as f:
            f.write(f"{url}\n")
            
        import webbrowser
        webbrowser.open(url)
        
        logger.info(f"Handoff URL received: {url}")
        return jsonify({"status": "success", "message": f"Opened {url}"})
    except Exception as e:
        logger.error(f"Handoff error: {e}")
        return jsonify({"error": str(e)}), 500

def start_server(sync_root_path, api_client, port=8080):
    global SYNC_ROOT, API_CLIENT
    SYNC_ROOT = sync_root_path
    API_CLIENT = api_client
    
    def run():
        logger.info(f"Starting Local API Bridge on port {port}")
        # Disable Flask banner
        cli = logging.getLogger('werkzeug')
        cli.setLevel(logging.ERROR)
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

    t = threading.Thread(target=run, daemon=True)
    t.start()