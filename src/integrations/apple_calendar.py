import logging
import datetime
from datetime import timedelta
from icalendar import Calendar, Event, vDatetime
import vobject

logger = logging.getLogger(__name__)

class AppleCalendar:
    def __init__(self, api_client):
        self.api = api_client
        
    def fetch_events(self, days_back=30, days_forward=90):
        if not hasattr(self.api, 'calendar'):
            return []
            
        start = datetime.datetime.now() - timedelta(days=days_back)
        end = datetime.datetime.now() + timedelta(days=days_forward)
        
        try:
            return self.api.calendar.get_events(start, end)
        except Exception as e:
            logger.error(f"Error fetching calendar events: {e}")
            return []

    def export_ics(self, events):
        """
        Convert list of pyicloud events to an ICS string.
        """
        cal = Calendar()
        cal.add('prodid', '-//Orchard//AppleCalendar//EN')
        cal.add('version', '2.0')
        
        for e in events:
            event = Event()
            event.add('summary', e.get('title', 'No Title'))
            
            # Dates
            # e['startDate'] is [YYYYMMDD, Y, M, D, H, M, ?]
            # We need to parse this.
            start_arr = e.get('startDate')
            end_arr = e.get('endDate')
            
            if start_arr and len(start_arr) >= 6:
                dt_start = datetime.datetime(start_arr[1], start_arr[2], start_arr[3], start_arr[4], start_arr[5])
                event.add('dtstart', dt_start)
            
            if end_arr and len(end_arr) >= 6:
                dt_end = datetime.datetime(end_arr[1], end_arr[2], end_arr[3], end_arr[4], end_arr[5])
                event.add('dtend', dt_end)
                
            location = e.get('location')
            if location:
                event.add('location', location)
                
            desc = e.get('description')
            if desc:
                event.add('description', desc)
                
            cal.add_component(event)
            
        return cal.to_ical().decode('utf-8')

    def export_markdown(self, events):
        """
        Convert events to a Markdown summary.
        """
        lines = ["# Calendar Events", ""]
        
        # Sort by date
        # startDate is a list, can be compared directly lexicographically usually
        # but let's be safe
        sorted_events = sorted(events, key=lambda x: x.get('startDate', []))
        
        current_date = None
        
        for e in sorted_events:
            start_arr = e.get('startDate')
            if not start_arr or len(start_arr) < 6: continue
            
            dt = datetime.datetime(start_arr[1], start_arr[2], start_arr[3], start_arr[4], start_arr[5])
            date_str = dt.strftime('%Y-%m-%d')
            time_str = dt.strftime('%H:%M')
            
            if date_str != current_date:
                lines.append(f"\n## {date_str}")
                current_date = date_str
            
            title = e.get('title', 'No Title')
            loc = f" @ {e.get('location')}" if e.get('location') else ""
            lines.append(f"- **{time_str}** {title}{loc}")
            
        return "\n".join(lines)
