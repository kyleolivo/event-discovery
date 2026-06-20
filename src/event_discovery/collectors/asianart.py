"""Collector for the Asian Art Museum calendar (calendar.asianart.org).

The calendar is a WordPress site with a custom event post type that isn't
exposed via REST API. We scrape the calendar listing page and individual
event pages for structured data.
"""

import html
import json
import re
import sqlite3
from datetime import datetime

import httpx

from event_discovery import db

_CALENDAR_URL = "https://calendar.asianart.org"
_VENUE = "Asian Art Museum"
_VENUE_ADDRESS = "200 Larkin St, San Francisco, CA 94102"

# Match date patterns like "SUN, JUNE 21" or "THU, JULY 2"
_DATE_RE = re.compile(
    r"(?:MON|TUE|WED|THU|FRI|SAT|SUN)[A-Z]*,?\s+"
    r"(\w+)\s+(\d{1,2})",
    re.IGNORECASE,
)

# Match time patterns like "10:30AM" or "5:00PM" or "12:00PM"
_TIME_RE = re.compile(r"(\d{1,2}:\d{2})\s*(AM|PM)", re.IGNORECASE)

# Match year or infer current year
_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def _fetch_page(url: str) -> str:
    resp = httpx.get(
        url,
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "EventDiscovery/0.1 (event aggregator)"},
    )
    resp.raise_for_status()
    return resp.text


def _clean_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = html.unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _extract_events(page_html: str) -> list[dict]:
    """Extract events from the calendar listing page."""
    events = []
    current_year = datetime.utcnow().year

    # Find event links and their surrounding context
    # Pattern: look for event links with titles and date info nearby
    event_pattern = re.compile(
        r'<a[^>]+href="(https://calendar\.asianart\.org/(?:event/[^"]+|\?p=\d+))"[^>]*>'
        r'(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )

    # Split page into event-like blocks for context
    # Look for sections that contain both a link and date info
    blocks = re.split(r'(?=<a[^>]+href="https://calendar\.asianart\.org/(?:event/|\?p=))', page_html)

    for block in blocks:
        link_match = event_pattern.search(block)
        if not link_match:
            continue

        url = link_match.group(1)
        title_html = link_match.group(2)
        title = _clean_html(title_html).strip()

        if not title or len(title) < 3:
            continue
        # Skip generic navigation links
        if title.lower() in ("read more", "learn more", "details", "register", "tickets"):
            continue

        # Extract date from surrounding block text
        block_text = _clean_html(block)
        date_match = _DATE_RE.search(block_text)
        if not date_match:
            continue

        month_str = date_match.group(1)
        day_str = date_match.group(2)

        # Determine year
        year_match = _YEAR_RE.search(block_text)
        year = int(year_match.group(1)) if year_match else current_year

        try:
            event_date = datetime.strptime(f"{month_str} {day_str} {year}", "%B %d %Y")
        except ValueError:
            try:
                event_date = datetime.strptime(f"{month_str} {day_str} {year}", "%b %d %Y")
            except ValueError:
                continue

        # Extract time
        time_match = _TIME_RE.search(block_text)
        if time_match:
            time_str = f"{time_match.group(1)} {time_match.group(2).upper()}"
            try:
                t = datetime.strptime(time_str, "%I:%M %p")
                utc_h = (t.hour + 7) % 24
                start_utc = event_date.strftime(f"%Y-%m-%dT{utc_h:02d}:{t.minute:02d}:00")
            except ValueError:
                start_utc = event_date.strftime("%Y-%m-%dT00:00:00")
        else:
            start_utc = event_date.strftime("%Y-%m-%dT00:00:00")

        # Check for cost info
        cost = None
        if "free" in block_text.lower():
            cost = "Free with admission"
        elif "sold out" in block_text.lower():
            cost = "Sold Out"

        # Deduplicate by URL + date
        ext_id = f"aam-{url.split('/')[-2] if '/event/' in url else url.split('=')[-1]}-{event_date.strftime('%Y%m%d')}"

        events.append({
            "external_id": ext_id,
            "title": title,
            "description": None,
            "start_utc": start_utc,
            "end_utc": None,
            "timezone": "America/Los_Angeles",
            "venue_name": _VENUE,
            "venue_address": _VENUE_ADDRESS,
            "url": url,
            "cost": cost,
            "image_url": None,
            "raw_json": json.dumps({"source_url": url}),
        })

    # Deduplicate by external_id (same event can appear multiple times)
    seen = set()
    unique = []
    for e in events:
        if e["external_id"] not in seen:
            seen.add(e["external_id"])
            unique.append(e)

    return unique


def sync(conn: sqlite3.Connection, name: str, site_url: str) -> tuple[int, int]:
    """Scrape Asian Art Museum calendar and upsert into the DB."""
    page_html = _fetch_page(_CALENDAR_URL)
    source_id = db.upsert_source(conn, name, site_url, "asianart")

    before = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    events = _extract_events(page_html)
    for event in events:
        db.upsert_event(conn, source_id, event)

    after = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    added = after - before
    updated = len(events) - added
    return added, updated
