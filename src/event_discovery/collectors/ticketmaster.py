"""Collector for events via the Ticketmaster Discovery API (v2).

Covers all major concert/theater/sports venues that sell through Ticketmaster,
Live Nation, or their affiliates — in SF this includes The Fillmore, The Warfield,
Chase Center, Davies Symphony Hall, War Memorial Opera House, Bill Graham Civic, etc.

Requires a free API key from https://developer.ticketmaster.com/
Set TICKETMASTER_API_KEY in your .env or environment.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Iterator

import httpx

from event_discovery import db

_API_URL = "https://app.ticketmaster.com/discovery/v2/events.json"

# SF Bay Area DMA ID; alternatively use city/stateCode/latlong
_DEFAULT_PARAMS = {
    "city": "San Francisco",
    "stateCode": "CA",
    "countryCode": "US",
    "size": 100,
    "sort": "date,asc",
}


def _get_api_key() -> str:
    key = os.environ.get("TICKETMASTER_API_KEY", "")
    if not key:
        raise RuntimeError(
            "TICKETMASTER_API_KEY not set. Get a free key at "
            "https://developer.ticketmaster.com/"
        )
    return key


def _collect_pages(api_key: str, extra_params: dict | None = None) -> Iterator[dict]:
    """Yield raw event dicts from every page of the Discovery API."""
    client = httpx.Client(timeout=30, follow_redirects=True)
    params = {**_DEFAULT_PARAMS, "apikey": api_key, "page": 0}
    if extra_params:
        params.update(extra_params)

    while True:
        resp = client.get(_API_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

        embedded = data.get("_embedded", {})
        events = embedded.get("events", [])
        if not events:
            break
        yield from events

        page_info = data.get("page", {})
        current = page_info.get("number", 0)
        total_pages = page_info.get("totalPages", 1)
        if current + 1 >= total_pages:
            break
        params["page"] = current + 1


def _format_cost(event: dict) -> str | None:
    ranges = event.get("priceRanges", [])
    if not ranges:
        return None
    r = ranges[0]
    low = r.get("min")
    high = r.get("max")
    currency = r.get("currency", "USD")
    if low and high and low != high:
        return f"${low:.0f}–${high:.0f} {currency}"
    if low:
        return f"${low:.0f} {currency}"
    return None


def _normalise(event: dict) -> dict:
    venues = (event.get("_embedded") or {}).get("venues", [])
    venue = venues[0] if venues else {}

    address_parts = filter(None, [
        (venue.get("address") or {}).get("line1"),
        (venue.get("city") or {}).get("name"),
        (venue.get("state") or {}).get("stateCode"),
        venue.get("postalCode"),
    ])

    dates = event.get("dates", {})
    start = dates.get("start", {})
    local_date = start.get("localDate", "")
    local_time = start.get("localTime", "")
    start_utc = f"{local_date}T{local_time}" if local_time else f"{local_date}T00:00:00"

    classifications = event.get("classifications", [{}])
    genre_parts = filter(None, [
        classifications[0].get("segment", {}).get("name") if classifications else None,
        classifications[0].get("genre", {}).get("name") if classifications else None,
    ])
    genre = " / ".join(genre_parts) or None

    images = event.get("images", [])
    image_url = images[0]["url"] if images else None

    return {
        "external_id": event["id"],
        "title": event.get("name", "").strip(),
        "description": genre,
        "start_utc": start_utc,
        "end_utc": None,
        "timezone": dates.get("timezone"),
        "venue_name": venue.get("name"),
        "venue_address": ", ".join(address_parts) or None,
        "url": event.get("url"),
        "cost": _format_cost(event),
        "image_url": image_url,
        "raw_json": json.dumps(event),
    }


def sync(conn: sqlite3.Connection, name: str, site_url: str) -> tuple[int, int]:
    """Pull SF events from Ticketmaster Discovery API and upsert into the DB."""
    api_key = _get_api_key()
    source_id = db.upsert_source(conn, name, site_url, "ticketmaster")

    before = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    total_seen = 0
    for raw in _collect_pages(api_key):
        db.upsert_event(conn, source_id, _normalise(raw))
        total_seen += 1

    after = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    added = after - before
    updated = total_seen - added
    return added, updated
