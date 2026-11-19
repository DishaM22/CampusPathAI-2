"""
Registration deadline rules (Asia/Kolkata).

All deadlines are stored on the event as an ISO string with the IST offset,
e.g. "2026-12-01T17:00:00+05:30". India has no daylight saving, so a fixed
+05:30 offset is exact and needs no tz database (which Windows lacks).

Pure functions only: no database, no FastAPI, so they are easy to test.
"""

import re
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")

_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", re.I)
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y")


def now_ist():
    return datetime.now(IST)


def to_iso(value):
    """aware datetime -> '2026-12-01T17:00:00+05:30' (always in IST)."""
    return value.astimezone(IST).isoformat(timespec="seconds")


def parse_deadline(value):
    """
    Text -> aware IST datetime. A value without an offset (what a browser
    datetime-local box sends) is read as IST. Raises ValueError if unusable.
    """
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text or text[-1] in "zZ":
            raise ValueError("Enter the registration deadline as a date and time.")
        try:
            parsed = datetime.fromisoformat(text.replace(" ", "T", 1))
        except ValueError:
            raise ValueError("Enter the registration deadline as a date and time.")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def event_start(date_text, time_text):
    """
    Start of the event in IST from the stored 'YYYY-MM-DD' and 'HH:MM - HH:MM'
    strings. If the date is unreadable returns None; if only the time is
    unreadable the event is taken to start at the very end of that day.
    """
    date_value = None
    for fmt in _DATE_FORMATS:
        try:
            date_value = datetime.strptime(str(date_text or "").strip()[:10], fmt).date()
            break
        except ValueError:
            continue
    if date_value is None:
        return None

    match = _TIME.search(str(time_text or ""))
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        meridiem = (match.group(3) or "").upper()
        if meridiem == "PM" and hour < 12:
            hour += 12
        elif meridiem == "AM" and hour == 12:
            hour = 0
        if hour < 24 and minute < 60:
            return datetime(date_value.year, date_value.month, date_value.day,
                            hour, minute, tzinfo=IST)

    return datetime(date_value.year, date_value.month, date_value.day,
                    23, 59, tzinfo=IST)


def deadline_problem(deadline, date_text, time_text, check_future=True, now=None):
    """
    Readable problem with a deadline, or "" when it is fine.
      - must be before the event starts
      - must still be in the future (only checked when it is being set/changed)
    """
    start = event_start(date_text, time_text)
    if start is not None and deadline >= start:
        return "The registration deadline must be before the event starts."
    if check_future and deadline <= (now or now_ist()):
        return "The registration deadline must be in the future."
    return ""


def stored_deadline(event):
    """Deadline of an event document as an aware datetime, or None."""
    raw = event.get("registration_deadline") if isinstance(event, dict) else None
    if not raw:
        return None
    try:
        return parse_deadline(raw)
    except ValueError:
        return None


def registration_is_closed(event, now=None):
    """
    True only when the event takes registrations, has a deadline and that
    deadline has passed. Events without a deadline never close by deadline.
    """
    if not isinstance(event, dict) or event.get("registration_required") is False:
        return False
    deadline = stored_deadline(event)
    return bool(deadline) and (now or now_ist()) > deadline