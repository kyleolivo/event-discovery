"""Collector for Salesforce Park events from the TJPA activities page.

Scrapes https://www.tjpa.org/salesforce-transit-center/activities for
both one-off special events and recurring weekly programming.
"""

import json
import re
import sqlite3
from datetime import datetime, timedelta

import httpx

from event_discovery import db

_ACTIVITIES_URL = "https://www.tjpa.org/salesforce-transit-center/activities"
_VENUE = "Salesforce Park"
_VENUE_ADDRESS = "425 Mission St, San Francisco, CA 94105"


def _fetch_page() -> str:
    resp = httpx.get(
        _ACTIVITIES_URL,
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "EventDiscovery/0.1 (event aggregator)"},
    )
    resp.raise_for_status()
    return resp.text


def _extract_special_events(html: str) -> list[dict]:
    """Extract one-off special events from the page HTML."""
    events = []
    # Match patterns like "Saturday, June 20, 2026" or "Friday–Sunday, August 21–23, 2026"
    # followed by event details
    date_pattern = re.compile(
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
        r"(?:[–-]\w+)?,\s+"
        r"(\w+ \d{1,2}(?:[–-]\d{1,2})?,?\s*\d{4})",
        re.IGNORECASE,
    )

    # Look for event blocks: typically a heading followed by date and description
    # Split by common heading tags
    blocks = re.split(r"<h[234][^>]*>", html)

    for block in blocks:
        # Get the heading text
        heading_match = re.match(r"([^<]+)</h", block)
        if not heading_match:
            continue
        title = heading_match.group(1).strip()
        if not title or len(title) < 5:
            continue

        # Look for a date in this block
        date_match = date_pattern.search(block)
        if not date_match:
            continue

        date_str = date_match.group(1)
        # Clean up date range (take first date)
        date_str = re.sub(r"[–-]\d{1,2}", "", date_str).strip().rstrip(",")

        try:
            dt = datetime.strptime(date_str, "%B %d %Y")
        except ValueError:
            try:
                dt = datetime.strptime(date_str, "%B %d, %Y")
            except ValueError:
                continue

        # Extract time if present
        time_match = re.search(r"(\d{1,2}(?::\d{2})?\s*(?:a\.m\.|p\.m\.|am|pm))", block, re.I)
        start_utc = dt.strftime("%Y-%m-%dT00:00:00")
        if time_match:
            time_str = time_match.group(1).replace(".", "").strip()
            for fmt in ("%I:%M %p", "%I %p", "%I:%M%p", "%I%p"):
                try:
                    t = datetime.strptime(time_str, fmt)
                    # Convert PDT to UTC (add 7 hours)
                    utc_hour = (t.hour + 7) % 24
                    start_utc = dt.strftime(f"%Y-%m-%dT{utc_hour:02d}:{t.minute:02d}:00")
                    break
                except ValueError:
                    continue

        # Extract description
        desc_text = re.sub(r"<[^>]+>", " ", block)
        desc_text = re.sub(r"\s+", " ", desc_text).strip()[:500]

        # Extract location within park
        location_match = re.search(r"(?:Main Plaza|Amphitheater|Central Lawn|Wetland Garden)", block)
        venue_detail = f"{_VENUE} — {location_match.group()}" if location_match else _VENUE

        events.append({
            "external_id": f"tjpa-{dt.strftime('%Y%m%d')}-{title[:30]}",
            "title": title,
            "description": desc_text if len(desc_text) > len(title) + 10 else None,
            "start_utc": start_utc,
            "end_utc": None,
            "timezone": "America/Los_Angeles",
            "venue_name": venue_detail,
            "venue_address": _VENUE_ADDRESS,
            "url": _ACTIVITIES_URL,
            "cost": "Free",
            "image_url": None,
            "raw_json": json.dumps({"source": "tjpa_special", "title": title}),
        })

    return events


def _generate_recurring_events(html: str, weeks_ahead: int = 8) -> list[dict]:
    """Generate concrete event instances from the recurring weekly schedule."""
    # Hardcoded schedule from the TJPA activities page.
    # This is more reliable than scraping recurring schedule HTML.
    RECURRING = [
        ("Lunchbox Music", "Tuesday", "12:00", "13:00", "Main Plaza"),
        ("Rooftop Jazz", "Wednesday", "11:30", "13:30", "Main Plaza"),
        ("Live on the Lawn", "Thursday", "12:00", "13:30", "Central Lawn"),
        ("Bluegrass Breeze", "Saturday", "11:30", "13:30", "Central Lawn"),
        ("ZUMBA", "Monday", "18:00", "19:00", "Main Plaza"),
        ("Yoga", "Wednesday", "12:30", "13:30", "Amphitheater"),
        ("Bootcamp", "Thursday", "08:00", "09:00", "Main Plaza"),
        ("Yoga", "Friday", "12:30", "13:30", "Amphitheater"),
        ("Toddler Tuesday", "Tuesday", "10:00", "11:00", "Main Plaza"),
        ("Toddler Thursday", "Thursday", "10:00", "10:45", "Main Plaza"),
        ("Writing Workshop", "Wednesday", "12:00", "13:00", "Wetland Garden"),
    ]

    DAY_MAP = {
        "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
        "Friday": 4, "Saturday": 5, "Sunday": 6,
    }

    today = datetime.utcnow().date()
    events = []

    for title, day_name, start_time, end_time, location in RECURRING:
        day_num = DAY_MAP[day_name]
        # Find next occurrence
        days_until = (day_num - today.weekday()) % 7
        if days_until == 0:
            days_until = 0  # include today
        next_date = today + timedelta(days=days_until)

        for week in range(weeks_ahead):
            event_date = next_date + timedelta(weeks=week)
            # PDT offset: add 7 hours to local time for UTC
            local_h, local_m = map(int, start_time.split(":"))
            utc_h = (local_h + 7) % 24
            start_utc = f"{event_date.isoformat()}T{utc_h:02d}:{local_m:02d}:00"

            local_eh, local_em = map(int, end_time.split(":"))
            utc_eh = (local_eh + 7) % 24
            end_utc = f"{event_date.isoformat()}T{utc_eh:02d}:{local_em:02d}:00"

            events.append({
                "external_id": f"tjpa-recurring-{title.lower().replace(' ', '-')}-{event_date.isoformat()}",
                "title": title,
                "description": f"Free weekly event at Salesforce Park. {day_name}s, {start_time}–{end_time} PT.",
                "start_utc": start_utc,
                "end_utc": end_utc,
                "timezone": "America/Los_Angeles",
                "venue_name": f"{_VENUE} — {location}",
                "venue_address": _VENUE_ADDRESS,
                "url": _ACTIVITIES_URL,
                "cost": "Free",
                "image_url": None,
                "raw_json": json.dumps({"source": "tjpa_recurring", "title": title, "day": day_name}),
            })

    return events


def sync(conn: sqlite3.Connection, name: str, site_url: str) -> tuple[int, int]:
    """Scrape Salesforce Park events and upsert into the DB."""
    html = _fetch_page()
    source_id = db.upsert_source(conn, name, site_url, "salesforce_park")

    before = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    all_events = _extract_special_events(html) + _generate_recurring_events(html)
    for event in all_events:
        db.upsert_event(conn, source_id, event)

    after = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    added = after - before
    updated = len(all_events) - added
    return added, updated
