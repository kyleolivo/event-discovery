"""Collector for SF Funcheap (sf.funcheap.com) via their RSS feed.

Funcheap publishes free/cheap SF Bay Area events as WordPress posts.
Event dates are embedded in post titles with a "M/D/YY:" prefix.
Full event details are on the linked page, but the RSS provides
title, date, categories, and the event URL.
"""

import html
import json
import re
import sqlite3
from datetime import datetime, timezone

import httpx
from xml.etree import ElementTree as ET

from event_discovery import db

_FEED_URL = "https://sf.funcheap.com/feed/"

_NAMESPACES = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

_DATE_PREFIX_RE = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2,4}):\s*")


def _parse_event_date(title: str) -> tuple[str | None, str]:
    """Extract date from title prefix like '8/11/26: Event Name'.

    Returns (iso_date_or_none, cleaned_title).
    """
    m = _DATE_PREFIX_RE.match(title)
    if not m:
        return None, title

    date_str = m.group(1)
    cleaned = title[m.end():].strip()

    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.year < 2000:
                dt = dt.replace(year=dt.year + 2000)
            return dt.strftime("%Y-%m-%dT00:00:00"), cleaned
        except ValueError:
            continue

    return None, title


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = re.sub(r"<[^>]+>", "", text)
    return html.unescape(cleaned).strip() or None


def _parse_cost(title: str) -> str | None:
    """Try to extract cost from title (e.g. '- $6' suffix or 'FREE')."""
    cost_match = re.search(r"-\s*(\$\d+(?:\.\d{2})?)\s*$", title)
    if cost_match:
        return cost_match.group(1)
    if "free" in title.lower():
        return "Free"
    return None


def _collect_items(feed_url: str) -> list[ET.Element]:
    resp = httpx.get(feed_url, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    return root.findall(".//item")


def _normalise(item: ET.Element) -> dict | None:
    raw_title = item.findtext("title", "").strip()
    raw_title = html.unescape(raw_title)

    event_date, title = _parse_event_date(raw_title)

    # Strip trailing cost from title (e.g. "- $6")
    title = re.sub(r"\s*-\s*\$\d+(?:\.\d{2})?\s*$", "", title).strip()
    # Strip trailing "- FREE"
    title = re.sub(r"\s*-\s*FREE\s*$", "", title, flags=re.IGNORECASE).strip()

    if not title:
        return None

    url = item.findtext("link", "").strip()
    guid = item.findtext("guid", url).strip()

    content = item.findtext("content:encoded", "", _NAMESPACES)
    description = _strip_html(content) if content else None

    categories = [c.text for c in item.findall("category") if c.text]
    # Filter out meta-categories
    display_cats = [c for c in categories if c not in ("*Top Pick*",)]

    cost = _parse_cost(raw_title)
    if not cost and any("free" in c.lower() for c in categories):
        cost = "Free"

    # Use pubDate as fallback if no date in title
    if not event_date:
        pub_date = item.findtext("pubDate", "")
        if pub_date:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(pub_date)
                event_date = dt.strftime("%Y-%m-%dT%H:%M:%S")
            except (ValueError, TypeError):
                pass

    if not event_date:
        return None

    return {
        "external_id": guid,
        "title": title,
        "description": (description or "")[:1000] or None,
        "start_utc": event_date,
        "end_utc": None,
        "timezone": "America/Los_Angeles",
        "venue_name": None,
        "venue_address": None,
        "url": url,
        "cost": cost,
        "image_url": None,
        "raw_json": json.dumps({
            "title": raw_title,
            "categories": categories,
            "guid": guid,
        }),
    }


def sync(conn: sqlite3.Connection, name: str, site_url: str) -> tuple[int, int]:
    """Pull events from SF Funcheap RSS feed and upsert into the DB."""
    source_id = db.upsert_source(conn, name, site_url, "funcheap")

    before = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    total_seen = 0
    for item in _collect_items(_FEED_URL):
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
