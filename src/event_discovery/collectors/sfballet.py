"""Collector for SF Ballet (sfballet.org/calendar/).

WordPress site with a custom calendar grid. We scrape the calendar page
which groups events by month and day, with event cards containing titles
and links to individual productions.
"""

import html
import json
import re
import sqlite3
from datetime import datetime

import httpx

from event_discovery import db

_CALENDAR_URL = "https://www.sfballet.org/calendar/"
_BASE_URL = "https://www.sfballet.org"
_VENUE = "War Memorial Opera House"
_VENUE_ADDRESS = "301 Van Ness Ave, San Francisco, CA 94102"

_SKIP_TITLES = {"pre-ballet classes for ages 2–8", "ballet classes for ages 9–13",
                "adult ballet: horton technique"}


def _fetch_page() -> str:
    resp = httpx.get(
        _CALENDAR_URL,
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "EventDiscovery/0.1 (event aggregator)"},
    )
    resp.raise_for_status()
    return resp.text


def _extract_events(page_html: str) -> list[dict]:
    """Extract performances from the calendar grid."""
    events = []

    month_pattern = re.compile(
        r'<div[^>]+class="month\s+month-(\w+)-(\d{4})[^"]*"[^>]*>(.*?)(?=<div[^>]+class="month\s+month-|$)',
        re.DOTALL,
    )

    day_pattern = re.compile(
        r'<div[^>]+class="day-name\s+calendar-day[^"]*"[^>]*>(.*?)(?=<div[^>]+class="day-name\s+calendar-day|$)',
        re.DOTALL,
    )

    day_num_pattern = re.compile(r'class="day-number[^"]*"[^>]*>\s*<h6>\s*(\d{1,2})\s*</h6>', re.DOTALL)

    event_card_pattern = re.compile(
        r'data-hover="([^"]+)".*?<a[^>]+href="(/productions/[^"]+)"',
        re.DOTALL,
    )

    time_pattern = re.compile(r'(\d{1,2}:\d{2})\s*(AM|PM|am|pm)', re.IGNORECASE)

    for month_match in month_pattern.finditer(page_html):
        month_name = month_match.group(1)
        year = int(month_match.group(2))
        month_html = month_match.group(3)

        try:
            month_num = datetime.strptime(month_name, "%B").month
        except ValueError:
            continue

        for day_match in day_pattern.finditer(month_html):
            day_html = day_match.group(1)

            num_match = day_num_pattern.search(day_html)
            if not num_match:
                continue
            day_num = int(num_match.group(1))

            try:
                dt = datetime(year, month_num, day_num)
            except ValueError:
                continue

            for card_match in event_card_pattern.finditer(day_html):
                title_raw = html.unescape(card_match.group(1)).strip()
                url_path = card_match.group(2)

                if title_raw.lower() in _SKIP_TITLES:
                    continue

                title = re.sub(r"(?<=\w)['’]S\b", lambda m: m.group().lower(), title_raw.title())

                time_match = time_pattern.search(day_html[card_match.start():card_match.end() + 200])
                if time_match:
                    time_str = f"{time_match.group(1)} {time_match.group(2).upper()}"
                    try:
                        t = datetime.strptime(time_str, "%I:%M %p")
                        utc_h = (t.hour + 7) % 24
                        start_utc = dt.strftime(f"%Y-%m-%dT{utc_h:02d}:{t.minute:02d}:00")
                    except ValueError:
                        start_utc = dt.strftime("%Y-%m-%dT00:00:00")
                else:
                    start_utc = dt.strftime("%Y-%m-%dT00:00:00")

                venue = _VENUE
                venue_addr = _VENUE_ADDRESS
                if "school" in title_raw.lower() or "class" in title_raw.lower():
                    venue = "San Francisco Ballet School"
                    venue_addr = "455 Franklin St, San Francisco, CA 94102"

                full_url = f"{_BASE_URL}{url_path}"
                ext_id = f"sfballet-{dt.strftime('%Y%m%d')}-{url_path.strip('/').replace('/', '-')}"

                events.append({
                    "external_id": ext_id,
                    "title": f"SF Ballet: {title}",
                    "description": None,
                    "start_utc": start_utc,
                    "end_utc": None,
                    "timezone": "America/Los_Angeles",
                    "venue_name": venue,
                    "venue_address": venue_addr,
                    "url": full_url,
                    "cost": None,
                    "image_url": None,
                    "raw_json": json.dumps({"production": title, "url_path": url_path}),
                })

    seen = set()
    unique = []
    for e in events:
        if e["external_id"] not in seen:
            seen.add(e["external_id"])
            unique.append(e)

    return unique


def sync(conn: sqlite3.Connection, name: str, site_url: str) -> tuple[int, int]:
    """Scrape SF Ballet calendar and upsert into the DB."""
    page_html = _fetch_page()
    source_id = db.upsert_source(conn, name, site_url, "sfballet")

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
