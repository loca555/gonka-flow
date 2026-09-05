"""One named timezone for displayed dates and calendar-day aggregates."""
from datetime import datetime
from zoneinfo import ZoneInfo

TIME_ZONE = "Asia/Nicosia"
CYPRUS = ZoneInfo(TIME_ZONE)

def local_day(ts):
    return datetime.fromtimestamp(ts, CYPRUS).strftime("%Y-%m-%d")

def local_time(ts):
    return datetime.fromtimestamp(ts, CYPRUS).isoformat(timespec="seconds")
