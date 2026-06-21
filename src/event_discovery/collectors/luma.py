"""Collector for Luma (lu.ma) events via the public API.

Supports two modes:
  1. Discover pages (e.g. lu.ma/sf) — uses the /url endpoint to get
     featured events for a city/region.
  2. Calendar pages (e.g. lu.ma/some-org) — uses /calendar/list-events
     with a calendar_api_id for a specific organization's calendar.

The public Luma API does not require authentication for public content.
"""

import json
import sqlite3

import httpx

from event_discovery import db

_API_BASE = "https://api.lu.ma"


def _fetch_discover_events(slug: str) -> list[dict]:
    """Fetch events from a Luma discover page (city/region)."""
    resp = httpx.get(
        f"{_API_BASE}/url",
        params={"url": slug},
        timeout=30,
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("kind") != "discover-place":
        return []

    events = []
    for entry in data.get("data", {}).get("events", []):
        event = entry.get("event") or entry
        if event:
            events.append(event)
    return events


def _fetch_calendar_events(calendar_api_id: str) -> list[dict]:
    """Page through events for a specific Luma calendar."""
    events: list[dict] = []
    params: dict = {"calendar_api_id": calendar_api_id, "pagination_limit": 50}
    client = httpx.Client(timeout=30, follow_redirects=True)

    while True:
        resp = client.get(
            f"{_API_BASE}/calendar/list-events",
            params=params,
            headers={"Accept": "application/json"},
        )
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

    geo = event.get("geo_address_info") or {}
    venue_name = geo.get("address") or None
    full_address = geo.get("full_address") or None
    if not full_address:
        location_parts = filter(None, [
            geo.get("address"),
            geo.get("city"),
            geo.get("region"),
        ])
        full_address = ", ".join(location_parts) or None

    luma_url = event.get("url", "")
    if luma_url and not luma_url.startswith("http"):
        luma_url = f"https://lu.ma/{luma_url}"

    return {
        "external_id": event.get("api_id", ""),
        "title": event.get("name", "").strip(),
        "description": (event.get("description_short") or event.get("description") or "").strip() or None,
        "start_utc": start_utc,
        "end_utc": end_utc,
        "timezone": event.get("timezone"),
        "venue_name": venue_name,
        "venue_address": full_address,
        "url": luma_url,
        "cost": None,
        "image_url": event.get("cover_url"),
        "raw_json": json.dumps(event),
    }


def sync(
    conn: sqlite3.Connection,
    name: str,
    site_url: str,
    source_config: dict | None = None,
) -> tuple[int, int]:
    """Pull events from a Luma discover page or calendar and upsert into the DB."""
    config = source_config or {}
    source_id = db.upsert_source(conn, name, site_url, "luma")

    slug = site_url.rstrip("/").rsplit("/", 1)[-1]

    if "calendar_api_id" in config:
        raw_events = _fetch_calendar_events(config["calendar_api_id"])
    else:
        raw_events = _fetch_discover_events(slug)

    before = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    for raw in raw_events:
        db.upsert_event(conn, source_id, _normalise(raw))

    after = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    added = after - before
    updated = len(raw_events) - added
    return added, updated
