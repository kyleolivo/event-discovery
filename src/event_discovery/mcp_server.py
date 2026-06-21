"""MCP server exposing SF event discovery as Claude tools.

Run with:
    events mcp-server

Then add to Claude Desktop's config (~/Library/Application Support/Claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "sf-events": {
          "command": "events",
          "args": ["mcp-server"]
        }
      }
    }
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from mcp.server.fastmcp import FastMCP

from event_discovery import db, ranker

mcp = FastMCP("SF Event Discovery")

_PDT = timezone(timedelta(hours=-7))


def _fmt_event(row, score: int | None = None, note: str | None = None) -> str:
    start = row["start_utc"] or ""
    try:
        dt = datetime.fromisoformat(start.replace(" ", "T")).replace(tzinfo=timezone.utc)
        date_str = dt.astimezone(_PDT).strftime("%a %b %-d, %-I:%M %p")
    except ValueError:
        date_str = start[:16]

    parts = [f"**{row['title']}**", date_str]
    if row["venue_name"]:
        parts.append(row["venue_name"])
    if row["source_name"]:
        parts.append(f"via {row['source_name']}")
    if score is not None:
        parts.append(f"relevance {score}/10")
    if note:
        parts.append(f"— {note}")
    if row["url"]:
        parts.append(row["url"])
    return " | ".join(parts)


@mcp.tool()
def list_events(days: int = 14, min_score: int = 5) -> str:
    """List upcoming SF events ranked by your saved preferences.

    Args:
        days: How many days ahead to look (default 14).
        min_score: Hide events with relevance below this 1–10 score (default 5).
    """
    with db.get_conn() as conn:
        events = db.get_upcoming_events(conn, days=days)
        if not events:
            return "No events found. Try running sync_events first."

        prefs = db.get_preferences(conn)
        if not prefs:
            lines = [_fmt_event(e) for e in events]
            return f"{len(lines)} upcoming events (no preferences set — showing all):\n\n" + "\n".join(lines)

        ranked = ranker.rank_events(events, prefs, days=days)
        ranked = [e for e in ranked if e["score"] >= min_score]

        if not ranked:
            return f"No events matched your interests at score ≥ {min_score} in the next {days} days."

        # Match ranked results back to full event rows for URLs
        url_map = {e["title"]: e["url"] for e in events}
        lines = []
        for e in ranked:
            url = e.get("url") or url_map.get(e["title"], "")
            line = f"**{e['title']}** | {e['date']} | score {e['score']}/10 — {e.get('note', '')}"
            if url:
                line += f"\n  {url}"
            lines.append(line)

        return f"{len(lines)} events matching your interests (next {days} days):\n\n" + "\n\n".join(lines)


@mcp.tool()
def search_events(query: str, days: int = 90) -> str:
    """Search upcoming SF events by keyword (matches title, description, venue).

    Args:
        query: Search term (e.g. "jazz", "ballet", "free", "outdoor").
        days: How many days ahead to search (default 90).
    """
    with db.get_conn() as conn:
        events = db.search_events(conn, query, days=days)

    if not events:
        return f"No events found matching '{query}' in the next {days} days."

    lines = [_fmt_event(e) for e in events]
    return f"{len(lines)} events matching '{query}':\n\n" + "\n".join(lines)


@mcp.tool()
def sync_events(source_name: str = "") -> str:
    """Pull the latest events from all configured sources (or one specific source).

    Args:
        source_name: Optional source name filter (e.g. "SF Jazz"). Leave empty to sync all.
    """
    # Import here to avoid circular deps
    from event_discovery.cli import DEFAULT_SOURCES
    from event_discovery.collectors import tribe_events, ical, luma, ticketmaster, funcheap, salesforce_park, cityarts, asianart, sfballet

    sources = DEFAULT_SOURCES
    if source_name:
        sources = [s for s in sources if source_name.lower() in s["name"].lower()]
        if not sources:
            return f"No source matching '{source_name}'. Available: {[s['name'] for s in DEFAULT_SOURCES]}"

    results = []
    with db.get_conn() as conn:
        for source in sources:
            try:
                kind = source["kind"]
                if kind == "tribe_events":
                    added, updated = tribe_events.sync(conn, source["name"], source["url"])
                elif kind == "ical":
                    added, updated = ical.sync(conn, source["name"], source["url"])
                elif kind == "luma":
                    added, updated = luma.sync(conn, source["name"], source["url"], source)
                elif kind == "ticketmaster":
                    added, updated = ticketmaster.sync(conn, source["name"], source["url"])
                elif kind == "funcheap":
                    added, updated = funcheap.sync(conn, source["name"], source["url"])
                elif kind == "cityarts":
                    added, updated = cityarts.sync(conn, source["name"], source["url"])
                elif kind == "asianart":
                    added, updated = asianart.sync(conn, source["name"], source["url"])
                elif kind == "salesforce_park":
                    added, updated = salesforce_park.sync(conn, source["name"], source["url"])
                elif kind == "sfballet":
                    added, updated = sfballet.sync(conn, source["name"], source["url"])
                else:
                    results.append(f"⚠ {source['name']}: unknown kind '{kind}'")
                    continue
                results.append(f"✓ {source['name']}: +{added} new, {updated} updated")
            except Exception as e:
                results.append(f"✗ {source['name']}: {e}")

    return "\n".join(results)


@mcp.tool()
def get_preferences() -> str:
    """Return your current event interest preferences."""
    with db.get_conn() as conn:
        prefs = db.get_preferences(conn)
    return prefs or "No preferences set. Use set_preferences to describe your interests."


@mcp.tool()
def set_preferences(preferences: str) -> str:
    """Update your event interest preferences used for ranking.

    Args:
        preferences: Free-text description of your interests (music genres, art forms, activities, etc.).
    """
    with db.get_conn() as conn:
        db.set_preferences(conn, preferences)
    return "Preferences saved."


def run():
    mcp.run()
