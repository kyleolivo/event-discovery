"""Collector for sites running The Events Calendar WordPress plugin.

Paginates through the /wp-json/tribe/events/v1/events REST API and
normalises each event to the common schema expected by db.upsert_event.
"""

import html
import json
import re
import sqlite3
from typing import Iterator

import httpx

from event_discovery import db


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = re.sub(r"<[^>]+>", "", text)
    return html.unescape(cleaned).strip() or None


def _collect_pages(base_url: str, per_page: int = 50) -> Iterator[dict]:
    """Yield raw event dicts from every page of the API."""
    client = httpx.Client(timeout=30, follow_redirects=True)
    params: dict = {"per_page": per_page, "page": 1, "status": "publish"}
    while True:
        resp = client.get(base_url, params=params)
        resp.raise_for_status()
        data = resp.json()
        events = data.get("events", [])
        if not events:
            break
        yield from events
        total_pages = data.get("total_pages", 1)
        if params["page"] >= total_pages:
            break
        params["page"] += 1


def _normalise(event: dict) -> dict:
    venue = event.get("venue") or {}
    address_parts = filter(None, [
        venue.get("address"),
        venue.get("city"),
        venue.get("state"),
        venue.get("zip"),
    ])
    venue_address = ", ".join(address_parts) or None

    image = event.get("image") or {}
    image_url = image.get("url") or None

    return {
        "external_id": str(event["id"]),
        "title": html.unescape(event.get("title", "").strip()),
        "description": _strip_html(event.get("description")),
        "start_utc": event.get("utc_start_date"),
        "end_utc": event.get("utc_end_date") or None,
        "timezone": event.get("timezone"),
        "venue_name": html.unescape(venue.get("venue") or "").strip() or None,
        "venue_address": venue_address,
        "url": event.get("url"),
        "cost": event.get("cost") or None,
        "image_url": image_url,
        "raw_json": json.dumps(event),
    }


def sync(conn: sqlite3.Connection, name: str, site_url: str) -> tuple[int, int]:
    """Pull all events from a Tribe Events site and upsert into the DB.

    Returns (new_count, updated_count) — approximate; both covered by upsert.
    """
    api_url = site_url.rstrip("/") + "/wp-json/tribe/events/v1/events"
    source_id = db.upsert_source(conn, name, site_url, "tribe_events")

    before = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    total_seen = 0
    for raw in _collect_pages(api_url):
        db.upsert_event(conn, source_id, _normalise(raw))
        total_seen += 1

    after = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    added = after - before
    updated = total_seen - added
    return added, updated
