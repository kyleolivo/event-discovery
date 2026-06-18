"""Collector for venues that publish a standard iCalendar (.ics) feed."""

import json
import sqlite3
from datetime import datetime, timezone

import httpx
from icalendar import Calendar

from event_discovery import db


def _to_utc_str(dt_val) -> str | None:
    if dt_val is None:
        return None
    if isinstance(dt_val, datetime):
        if dt_val.tzinfo is None:
            dt_val = dt_val.replace(tzinfo=timezone.utc)
        return dt_val.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    # date-only
    return dt_val.strftime("%Y-%m-%dT00:00:00")


def _normalise(component) -> dict | None:
    title = str(component.get("SUMMARY", "")).strip()
    if not title:
        return None

    dtstart = component.get("DTSTART")
    if not dtstart:
        return None

    start_utc = _to_utc_str(dtstart.dt)
    if not start_utc:
        return None

    dtend = component.get("DTEND")
    end_utc = _to_utc_str(dtend.dt) if dtend else None

    uid = str(component.get("UID", "")).strip()
    url = str(component.get("URL", "")).strip() or None
    description = str(component.get("DESCRIPTION", "")).strip() or None
    location = str(component.get("LOCATION", "")).strip() or None

    return {
        "external_id": uid or f"{start_utc}:{title[:40]}",
        "title": title,
        "description": description,
        "start_utc": start_utc,
        "end_utc": end_utc,
        "timezone": None,
        "venue_name": location,
        "venue_address": location,
        "url": url,
        "cost": None,
        "image_url": None,
        "raw_json": json.dumps({"uid": uid, "location": location}),
    }


def sync(conn: sqlite3.Connection, name: str, site_url: str) -> tuple[int, int]:
    """Pull events from an iCal feed URL and upsert into the DB."""
    resp = httpx.get(site_url, timeout=30, follow_redirects=True)
    resp.raise_for_status()

    cal = Calendar.from_ical(resp.content)
    source_id = db.upsert_source(conn, name, site_url, "ical")

    before = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    total_seen = 0
    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        event = _normalise(component)
        if event:
            db.upsert_event(conn, source_id, event)
            total_seen += 1

    after = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id = ?", (source_id,)
    ).fetchone()[0]

    added = after - before
    updated = total_seen - added
    return added, updated
