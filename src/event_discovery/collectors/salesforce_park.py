"""Collector for Salesforce Park events.

Special events come from TJPA's public Google Calendar (iCal feed).
Regular weekly programming is generated from the known recurring schedule
since it's not in the Google Calendar.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
from icalendar import Calendar

from event_discovery import db

_ICAL_URL = "https://calendar.google.com/calendar/ical/tjpa.info.1%40gmail.com/public/basic.ics"
_ACTIVITIES_URL = "https://www.tjpa.org/salesforce-transit-center/activities"
_VENUE = "Salesforce Park"
_VENUE_ADDRESS = "425 Mission St, San Francisco, CA 94105"


def _fetch_ical_events() -> list[dict]:
    """Pull special events from TJPA's public Google Calendar."""
    resp = httpx.get(_ICAL_URL, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    cal = Calendar.from_ical(resp.content)

    today = datetime.utcnow().date()
    events = []

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        title = str(component.get("SUMMARY", "")).strip()
        if not title:
            continue

        dtstart = component.get("DTSTART")
        if not dtstart:
            continue

        dt = dtstart.dt
        event_date = dt.date() if hasattr(dt, "date") else dt
        if event_date < today:
            continue

        if hasattr(dt, "hour"):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            start_utc = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        else:
            start_utc = f"{event_date.isoformat()}T00:00:00"

        dtend = component.get("DTEND")
        end_utc = None
        if dtend:
            dte = dtend.dt
            if hasattr(dte, "hour"):
                if dte.tzinfo is None:
                    dte = dte.replace(tzinfo=timezone.utc)
                end_utc = dte.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        location = str(component.get("LOCATION", "")).strip()
        description = str(component.get("DESCRIPTION", "")).strip() or None
        uid = str(component.get("UID", "")).strip()

        # Extract sub-location from the location string
        venue_name = _VENUE
        if location:
            venue_name = location if "Salesforce" in location else f"{_VENUE} — {location}"

        events.append({
            "external_id": uid or f"tjpa-gcal-{event_date.isoformat()}-{title[:30]}",
            "title": title,
            "description": description[:500] if description else None,
            "start_utc": start_utc,
            "end_utc": end_utc,
            "timezone": "America/Los_Angeles",
            "venue_name": venue_name,
            "venue_address": _VENUE_ADDRESS,
            "url": _ACTIVITIES_URL,
            "cost": "Free",
            "image_url": None,
            "raw_json": json.dumps({"source": "tjpa_gcal", "uid": uid}),
        })

    return events


def _generate_recurring_events(weeks_ahead: int = 8) -> list[dict]:
    """Generate concrete event instances from the known recurring weekly schedule.

    The regular weekly programming is not in the Google Calendar, so we
    generate it from the published schedule on the TJPA activities page.
    """
    RECURRING = [
        # Live Music
        ("Lunchbox Music", "Tuesday", "12:00", "13:00", "Main Plaza"),
        ("Rooftop Jazz", "Wednesday", "11:30", "13:30", "Main Plaza"),
        ("Live on the Lawn", "Thursday", "12:00", "13:30", "Central Lawn"),
        ("Bluegrass Breeze", "Saturday", "11:30", "13:30", "Central Lawn"),
        # Fitness & Wellness
        ("ZUMBA", "Monday", "18:00", "19:00", "Main Plaza"),
        ("Yoga", "Wednesday", "12:30", "13:30", "Amphitheater"),
        ("Bootcamp", "Thursday", "08:00", "09:00", "Main Plaza"),
        ("High Fitness", "Thursday", "13:00", "14:00", "Main Plaza"),
        ("Yoga", "Friday", "12:30", "13:30", "Amphitheater"),
        ("Metcon", "Saturday", "10:00", "11:00", "Main Plaza"),
        # Children & Families
        ("Toddler Tuesday", "Tuesday", "10:00", "11:00", "Main Plaza"),
        ("Toddler Thursday", "Thursday", "10:00", "10:45", "Main Plaza"),
        # Arts & Culture
        ("Writing Workshop", "Wednesday", "12:00", "13:00", "Wetland Garden"),
        ("Words and Stories", "Wednesday", "13:00", "13:30", "Wetland Garden"),
    ]

    MONTHLY_RECURRING = [
        ("Family Storytime", "Wednesday", "10:00", "10:30", "Main Plaza", {1, 3}),
        ("Whimsy Wednesday", "Wednesday", "10:00", "11:30", "Kid's Play Area", {2, 4}),
        ("Plaza Pulse", "Thursday", "16:30", "18:30", "Main Plaza", {1, 3}),
        ("Drum Circle", "Sunday", "12:00", "13:30", "Amphitheater", {4}),
        ("Dave Parker Sextet", "Friday", "11:00", "12:30", "Main Plaza", {4}),
        ("Bollywood Nights", "Friday", "17:30", "19:00", "Main Plaza", {1, 3}),
        ("Argentine Tango", "Sunday", "12:00", "15:00", "Main Plaza", {3}),
        ("Birding Walks", "Wednesday", "08:00", "09:00", "Main Plaza", {1}),
        ("Garden Tours", "Wednesday", "10:00", "11:30", "Main Plaza", {4}),
    ]

    DAY_MAP = {
        "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
        "Friday": 4, "Saturday": 5, "Sunday": 6,
    }

    today = datetime.utcnow().date()
    events = []

    def _make_event(title, day_name, start_time, end_time, location, event_date, freq="weekly"):
        local_h, local_m = map(int, start_time.split(":"))
        utc_h = (local_h + 7) % 24
        start_utc = f"{event_date.isoformat()}T{utc_h:02d}:{local_m:02d}:00"

        local_eh, local_em = map(int, end_time.split(":"))
        utc_eh = (local_eh + 7) % 24
        end_utc = f"{event_date.isoformat()}T{utc_eh:02d}:{local_em:02d}:00"

        return {
            "external_id": f"tjpa-recurring-{title.lower().replace(' ', '-')}-{event_date.isoformat()}",
            "title": title,
            "description": f"Free {freq} event at Salesforce Park. {day_name}s, {start_time}–{end_time} PT.",
            "start_utc": start_utc,
            "end_utc": end_utc,
            "timezone": "America/Los_Angeles",
            "venue_name": f"{_VENUE} — {location}",
            "venue_address": _VENUE_ADDRESS,
            "url": _ACTIVITIES_URL,
            "cost": "Free",
            "image_url": None,
            "raw_json": json.dumps({"source": "tjpa_recurring", "title": title, "day": day_name}),
        }

    for title, day_name, start_time, end_time, location in RECURRING:
        day_num = DAY_MAP[day_name]
        days_until = (day_num - today.weekday()) % 7
        next_date = today + timedelta(days=days_until)
        for week in range(weeks_ahead):
            event_date = next_date + timedelta(weeks=week)
            events.append(_make_event(title, day_name, start_time, end_time, location, event_date))

    for title, day_name, start_time, end_time, location, which_weeks in MONTHLY_RECURRING:
        day_num = DAY_MAP[day_name]
        days_until = (day_num - today.weekday()) % 7
        next_date = today + timedelta(days=days_until)
        for week in range(weeks_ahead):
            event_date = next_date + timedelta(weeks=week)
            week_of_month = (event_date.day - 1) // 7 + 1
            if week_of_month in which_weeks:
                events.append(_make_event(title, day_name, start_time, end_time, location, event_date, "monthly"))

    return events


def sync(conn: sqlite3.Connection, name: str, site_url: str) -> tuple[int, int]:
    """Pull Salesforce Park events from Google Calendar + recurring schedule."""
    source_id = db.upsert_source(conn, name, site_url, "salesforce_park")

    before = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    all_events = _fetch_ical_events() + _generate_recurring_events()
    for event in all_events:
        db.upsert_event(conn, source_id, event)

    after = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    added = after - before
    updated = len(all_events) - added
    return added, updated
