"""Collector for City Arts & Lectures (cityarts.net) via RSS feed.

Event dates and venues are embedded in the RSS description field with a
consistent format: "Speaker Name DayOfWeek, Month DD, YYYY 7:30pm Pacific Time
Venue: Sydney Goldstein Theater"
"""

import html
import json
import re
import sqlite3
from datetime import datetime
from xml.etree import ElementTree as ET

import httpx

from event_discovery import db

_FEED_URL = "https://www.cityarts.net/feed/"

_NAMESPACES = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

_DATE_RE = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+"
    r"(\w+ \d{1,2},?\s*\d{4})",
    re.IGNORECASE,
)

_TIME_RE = re.compile(r"(\d{1,2}(?::\d{2})?\s*(?:am|pm))", re.IGNORECASE)

_VENUE_RE = re.compile(r"Venue:\s*(.+?)(?:\s*<|\s*$)", re.IGNORECASE)


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _parse_event_date(text: str) -> str | None:
    m = _DATE_RE.search(text)
    if not m:
        return None
    date_str = m.group(1).strip().rstrip(",")
    for fmt in ("%B %d, %Y", "%B %d %Y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            # Check for time
            tm = _TIME_RE.search(text)
            if tm:
                time_str = tm.group(1).strip()
                for tfmt in ("%I:%M%p", "%I:%Mpm", "%I:%Mam", "%I%p", "%I:%M %p", "%I %p"):
                    try:
                        t = datetime.strptime(time_str, tfmt)
                        # Convert PT to UTC (add 7 for PDT)
                        utc_h = (t.hour + 7) % 24
                        return dt.strftime(f"%Y-%m-%dT{utc_h:02d}:{t.minute:02d}:00")
                    except ValueError:
                        continue
            return dt.strftime("%Y-%m-%dT00:00:00")
        except ValueError:
            continue
    return None


def _parse_venue(text: str) -> str | None:
    m = _VENUE_RE.search(text)
    if m:
        return html.unescape(m.group(1).strip())
    if "sydney goldstein" in text.lower():
        return "Sydney Goldstein Theater"
    if "nourse" in text.lower():
        return "Nourse Theater"
    return None


def _normalise(item: ET.Element) -> dict | None:
    raw_title = html.unescape(item.findtext("title", "").strip())
    if not raw_title:
        return None

    url = item.findtext("link", "").strip()
    guid = item.findtext("guid", url).strip()

    desc = item.findtext("description", "") or ""
    content = item.findtext("content:encoded", "", _NAMESPACES) or ""
    full_text = desc + " " + content

    event_date = _parse_event_date(full_text)
    if not event_date:
        return None

    venue = _parse_venue(full_text)
    description = _strip_html(desc)

    return {
        "external_id": guid,
        "title": raw_title,
        "description": (description or "")[:1000] or None,
        "start_utc": event_date,
        "end_utc": None,
        "timezone": "America/Los_Angeles",
        "venue_name": venue,
        "venue_address": None,
        "url": url,
        "cost": None,
        "image_url": None,
        "raw_json": json.dumps({"guid": guid, "venue": venue}),
    }


def sync(conn: sqlite3.Connection, name: str, site_url: str) -> tuple[int, int]:
    """Pull events from City Arts & Lectures RSS feed."""
    resp = httpx.get(_FEED_URL, timeout=30, follow_redirects=True)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    source_id = db.upsert_source(conn, name, site_url, "cityarts")

    before = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    total_seen = 0
    for item in root.findall(".//item"):
        event = _normalise(item)
        if event:
            db.upsert_event(conn, source_id, event)
            total_seen += 1

    after = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    added = after - before
    updated = total_seen - added
    return added, updated
