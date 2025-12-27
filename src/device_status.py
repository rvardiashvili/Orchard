import logging

logger = logging.getLogger(__name__)

def get_devices(api):
    """
    Fetches the list of devices and their status from iCloud.
    """
    if not api:
        logger.debug("API object is None")
        return []
    
    devices = []
    
    # Strategy 1: Find My iPhone Service (Standard Library Way)
    try:
        if hasattr(api, 'iphone'):
            try:
                for dev in api.iphone.devices:
                    st = dev.status()
                    batt = st.get('batteryLevel', 0)
                    if batt <= 1.0: batt = int(batt * 100)
                    devices.append({
                        "name": st.get('name', 'Unknown'),
                        "model": st.get('deviceDisplayName', 'Apple Device'),
                        "battery": batt,
                        "is_mac": st.get('isMac', False),
                        "id": dev.content['id']
                    })
            except Exception as e:
                pass 
    except Exception:
        pass

    logger.debug(f"Devices after Strat 1: {len(devices)}")
    
    # Strategy 1.5: Manual "Find My" POST Request
    if not devices:
        try:
            webservices = getattr(api, 'webservices', None)
            if not webservices and hasattr(api, 'data') and isinstance(api.data, dict):
                webservices = api.data.get('webservices')
                
            if webservices and 'findme' in webservices:
                fmip_url = webservices['findme']['url']
                if fmip_url.endswith(':443'):
                    fmip_url = fmip_url[:-4]
                    
                init_url = f"{fmip_url}/fmipservice/client/web/initClient"
                
                # Headers needed for FindMy
                api.session.headers.update({
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
                
                logger.debug(f"Posting to FindMy: {init_url}")
                res = api.session.post(init_url, json=payload)
                
                if res.ok:
                    data = res.json()
                    content = data.get('content', [])
                    logger.debug(f"FindMy POST returned {len(content)} devices.")
                    
                    for dev in content:
                        # Normalize battery
                        batt = dev.get('batteryLevel', 0)
                        if batt <= 1.0: batt = int(batt * 100)
                        
                        devices.append({
                            "name": dev.get('name', 'Unknown'),
                            "model": dev.get('deviceDisplayName', 'Apple Device'),
                            "battery": batt,
                            "is_mac": 'Mac' in dev.get('deviceDisplayName', ''),
                            "id": dev.get('id')
                        })
                else:
                    logger.debug(f"FindMy POST failed: {res.status_code}")
                    
        except Exception as e:
            logger.debug(f"FindMy Manual Strat failed: {e}")

    logger.debug(f"Devices after Strat 1.5: {len(devices)}")
    
    # Strategy 2: Manual Call to Settings Web Service (Fallback)
    
    # Check for webservices in attribute OR inside .data dict
    webservices = None
    if hasattr(api, 'webservices'):
        try:
            webservices = api.webservices
        except:
            pass
            
    if not webservices and hasattr(api, 'data') and isinstance(api.data, dict):
        webservices = api.data.get('webservices')

    has_settings = webservices and 'settings' in webservices
    
    if not devices and has_settings:
        try:
            settings_url = webservices['settings']['url']
            if settings_url.endswith(':443'):
                settings_url = settings_url[:-4]
            
            url = f"{settings_url}/v1/devices"
            
            # Add required parameters (often causes 4xx if missing)
            params = {}
            if hasattr(api, 'params'):
                params = api.params
            
            logger.debug(f"Fetching devices from {url}")
            
            res = api.session.get(url, params=params)
            
            if res.ok:
                data = res.json()
                fetched_devs = data.get('devices', [])
                logger.debug(f"Devices found in JSON: {len(fetched_devs)}")
                
                for dev in fetched_devs:
                    devices.append({
                        "name": dev.get('name', 'Unknown Device'),
                        "model": dev.get('model', 'Apple Device'),
                        "battery": -1, 
                        "is_mac": 'Mac' in dev.get('model', ''),
                        "id": dev.get('deviceId')
                    })
            else:
                logger.debug(f"Failed response: {res.text[:100]}")
                
        except Exception as e:
            logger.debug(f"Exception in Strat 2: {repr(e)}")

    # Strategy 3: Parse 'ICDRSCapableDeviceList' from data (Emergency Fallback)
    if not devices and hasattr(api, 'data') and isinstance(api.data, dict):
        try:
            ds_info = api.data.get('dsInfo', {})
            dev_list_str = ds_info.get('ICDRSCapableDeviceList', '')
            
            if dev_list_str:
                logger.debug(f"Found device types in data: {dev_list_str}")
                for dev_type in dev_list_str.split(','):
                    dev_type = dev_type.strip()
                    if dev_type:
                        devices.append({
                            "name": f"My {dev_type.capitalize()}",
                            "model": f"Apple {dev_type.capitalize()}",
                            "battery": -1,
                            "is_mac": 'mac' in dev_type.lower(),
                            "id": "generic_" + dev_type
                        })
        except Exception as e:
            logger.debug(f"Strat 3 failed: {e}")

    # Strategy 4: Local Fallback
    if not devices:
        devices.append({
            "name": "Current Session",
            "model": "Linux (UnixSync)",
            "battery": 100,
            "is_mac": False,
            "id": "local"
        })

    return devices

def play_sound(api, device_id):
    """
    Plays a sound on the specified device.
    """
    if device_id.startswith("generic_"):
        logger.warning("Cannot ping generic device type (API access restricted).")
        return False
        
    if device_id == "local":
        # Beep on Linux
        print("\a") 
        return True

    try:
        # We need the 'Find My' service wrapper to play sound.
        # Since api.iphone is failing, we can try to manually call the findme endpoint
        # BUT that requires complex headers/state.
        
        # If the user has api.iphone working (unlikely given logs), use it:
        # Wrap in try-except because api.iphone might be an AppleDevice object (no .devices)
        # or raise PyiCloudNoDevicesException
        try:
            if hasattr(api, 'iphone') and hasattr(api.iphone, 'devices'):
                for dev in api.iphone.devices:
                    if dev.content['id'] == device_id:
                        dev.play_sound()
                        return True
        except Exception as e:
            pass # Fallthrough to manual strategy if library wrapper fails

        # If we reached here, we can't play sound via library.
        # Try Manual POST to FindMy service
        try:
            webservices = getattr(api, 'webservices', None)
            if not webservices and hasattr(api, 'data') and isinstance(api.data, dict):
                webservices = api.data.get('webservices')

            if webservices and 'findme' in webservices:
                fmip_url = webservices['findme']['url']
                if fmip_url.endswith(':443'):
                    fmip_url = fmip_url[:-4]
                
                # playSound endpoint
                sound_url = f"{fmip_url}/fmipservice/client/web/playSound"
                
                # Ensure headers are set
                api.session.headers.update({
                    'Origin': 'https://www.icloud.com',
                    'Referer': 'https://www.icloud.com/',
                })

                payload = {
                    "device": device_id,
                    "subject": "UnixSync Ping",
                    "clientContext": {
                        "appName": "iCloud Find (Web)",
                        "appVersion": "2.0",
                        "apiVersion": "3.0",
                        "fmly": True
                    }
                }
                
                logger.info(f"Manually sending sound request to {device_id}...")
                res = api.session.post(sound_url, json=payload)
                
                
                if res.ok:
                    logger.info("Sound request sent successfully.")
                    return True
                else:
                    logger.warning(f"Sound request failed: {res.text[:300]}")
                    return False

        except Exception as e:
            logger.error(f"Manual sound ping failed: {e}")

        logger.warning(f"Ping not supported for device {device_id} in current API state.")
        return False
        
    except Exception as e:
        logger.error(f"Failed to play sound: {e}")
        return False
