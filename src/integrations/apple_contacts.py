import logging
import base64
import vobject
import requests

logger = logging.getLogger(__name__)

class AppleContacts:
    def __init__(self, api_client):
        self.api = api_client
    
    def fetch_all(self):
        if not hasattr(self.api, 'contacts'):
            return []
        try:
            return self.api.contacts.all
        except Exception as e:
            logger.error(f"Error fetching contacts: {e}")
            return []

    def export_vcard(self, contact):
        """
        Convert a pyicloud contact dict to a vCard string.
        """
        v = vobject.vCard()
        
        # Name
        v.add('n')
        v.n.value = vobject.vcard.Name(
            family=contact.get('lastName', ''),
            given=contact.get('firstName', '')
        )
        v.add('fn')
        full_name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()
        if not full_name:
            full_name = contact.get('companyName', 'Unknown')
        v.fn.value = full_name
        
        # Emails
        for e in contact.get('emailAddresses', []):
            email = v.add('email')
            email.value = e.get('field', '')
            label = e.get('label', 'HOME').upper()
            email.type_param = label
            
        # Phones
        for p in contact.get('phones', []):
            tel = v.add('tel')
            tel.value = p.get('field', '')
            label = p.get('label', 'CELL').upper()
            tel.type_param = label
            
        # Addresses
        for a in contact.get('streetAddresses', []):
            adr = v.add('adr')
            # streetAddresses usually has 'field' which is the full formatted address
            # OR structure: street, city, state, postalCode, country
            # pyicloud structure might vary. Let's assume standard keys if available.
            # If 'field' is a string (often newline separated), we put it in street.
            # Actually pyicloud often returns a dict if structured, or list of dicts.
            # The dict usually has 'field', 'label', 'street', 'city', 'state', 'zip', 'countryCode'
            
            adr.value = vobject.vcard.Address(
                street=a.get('street', a.get('field', '')),
                city=a.get('city', ''),
                region=a.get('state', ''),
                code=a.get('zip', ''),
                country=a.get('country', '')
            )
            label = a.get('label', 'HOME').upper()
            adr.type_param = label

        # URLs
        for u in contact.get('urls', []):
            url = v.add('url')
            url.value = u.get('field', '')
            
        # Birthday
        bday = contact.get('birthday') # usually YYYY-MM-DD string or similar
        if bday:
            v.add('bday')
            v.bday.value = bday

        # Photo
        photo = contact.get('photo')
        if photo and photo.get('url'):
            try:
                # Fetch image
                res = self.api.session.get(photo['url'], stream=True, timeout=10)
                if res.status_code == 200:
                    # vobject handles encoding? No, usually we pass raw bytes or base64 string
                    # vCard 3.0 uses PHOTO;ENCODING=b;TYPE=JPEG:base64...
                    # vobject might need help.
                    
                    # Manual addition for safety
                    # content = base64.b64encode(res.content).decode('utf-8')
                    # p = v.add('photo')
                    # p.value = content
                    # p.type_param = 'JPEG'
                    # p.encoding_param = 'b'
                    
                    # Let's try vobject native support if possible, else manual string manipulation later?
                    # vobject expects binary data for value if encoding is 'b'
                    p = v.add('photo')
                    p.value = res.content
                    p.type_param = 'JPEG'
                    p.encoding_param = 'b'
            except Exception as e:
                logger.warning(f"Failed to fetch photo for {full_name}: {e}")

        return v.serialize()
