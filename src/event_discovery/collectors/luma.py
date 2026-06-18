"""Collector for Luma (lu.ma) calendars via the public API.

Each source entry should have kind="luma" and a url of the form:
  https://lu.ma/<calendar-slug>
or supply a calendar_id directly in the source config.

The public Luma API does not require authentication for public calendars.
"""

import json
import sqlite3

import httpx

from event_discovery import db

_API_BASE = "https://api.lu.ma/public/v1"


def _fetch_events(calendar_id: str) -> list[dict]:
    """Page through all upcoming events for a Luma calendar."""
    events: list[dict] = []
    params: dict = {"calendar_id": calendar_id, "pagination_limit": 50}
    client = httpx.Client(timeout=30, follow_redirects=True)

    while True:
        resp = client.get(f"{_API_BASE}/calendar/list-events", params=params)
        resp.raise_for_status()
        data = resp.json()

        for entry in data.get("entries", []):
            event = entry.get("event")
            if event:
                events.append(event)

        if not data.get("has_more"):
            break
        params["pagination_cursor"] = data["next_cursor"]

    return events


def _normalise(event: dict) -> dict:
    start_utc = (event.get("start_at") or "").replace("Z", "").replace("+00:00", "")
    end_at = event.get("end_at")
    end_utc = end_at.replace("Z", "").replace("+00:00", "") if end_at else None

    geo = event.get("geo_address_json") or {}
    location_parts = filter(None, [
        geo.get("address"),
        geo.get("city"),
        geo.get("region"),
    ])
    venue_address = ", ".join(location_parts) or None

    return {
        "external_id": event["api_id"],
        "title": event.get("name", "").strip(),
        "description": (event.get("description_short") or "").strip() or None,
        "start_utc": start_utc,
        "end_utc": end_utc,
        "timezone": event.get("timezone"),
        "venue_name": geo.get("address") or None,
        "venue_address": venue_address,
        "url": event.get("url"),
        "cost": None,
        "image_url": event.get("cover_url"),
        "raw_json": json.dumps(event),
    }


def _resolve_calendar_id(site_url: str, source_config: dict) -> str:
    """Extract calendar_id from source config or derive from slug in URL."""
    if "calendar_id" in source_config:
        return source_config["calendar_id"]
    # Treat the path component of the URL as the calendar slug,
    # e.g. https://lu.ma/sf -> "sf"
    slug = site_url.rstrip("/").rsplit("/", 1)[-1]
    return slug


def sync(
    conn: sqlite3.Connection,
    name: str,
    site_url: str,
    source_config: dict | None = None,
) -> tuple[int, int]:
    """Pull events from a Luma calendar and upsert into the DB."""
    calendar_id = _resolve_calendar_id(site_url, source_config or {})
    source_id = db.upsert_source(conn, name, site_url, "luma")

    before = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    events = _fetch_events(calendar_id)
    for raw in events:
        db.upsert_event(conn, source_id, _normalise(raw))

    after = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    added = after - before
    updated = len(events) - added
    return added, updated
