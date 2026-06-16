"""LLM-based event ranking using the Anthropic API."""

import json
import os
import sqlite3

import anthropic

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _event_summary(index: int, row: sqlite3.Row) -> dict:
    return {
        "index": index,
        "title": row["title"],
        "date": row["start_utc"][:10],
        "time": row["start_utc"][11:16] + " UTC" if row["start_utc"] else None,
        "end": row["end_utc"][:16] if row["end_utc"] else None,
        "venue": row["venue_name"],
        "cost": row["cost"] or "Free",
        "description": (row["description"] or "")[:400],
        "url": row["url"],
        "source": row["source_name"],
    }


MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")

RANKING_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "score": {"type": "integer"},
                    "note": {"type": "string"},
                },
                "required": ["index", "score", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["events"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are an SF event concierge. Given a list of upcoming events and the user's
interest profile, rank the events from most to least relevant. Each event has a numeric `index`.
For each event, return its `index`, a relevance `score` from 1–10, and a one-sentence `note`
explaining why it matches (or doesn't match) the user's interests. Be concise and direct. Skip
events with a score below 4 unless the list is short.

Refer to events only by their `index` — do not echo titles or URLs. Return events in the requested
JSON schema, ordered from most to least relevant.
"""


def rank_events(
    events: list[sqlite3.Row],
    preferences: str,
    days: int = 60,
) -> list[dict]:
    if not events:
        return []

    summaries = [_event_summary(i, e) for i, e in enumerate(events)]
    user_message = f"""User interests:
{preferences}

Upcoming events (next {days} days):
{json.dumps(summaries, indent=2)}
"""

    client = _get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": RANKING_SCHEMA,
            },
        },
    )

    text = next(block.text for block in response.content if block.type == "text")
    payload = json.loads(text)

    # Map the model's scores back onto the authoritative DB rows so titles,
    # dates, and URLs come from the database rather than the model echoing them.
    ranked = []
    for item in payload["events"]:
        index = item["index"]
        if not 0 <= index < len(events):
            continue
        row = events[index]
        ranked.append({
            "title": row["title"],
            "date": row["start_utc"][:10],
            "url": row["url"],
            "score": item["score"],
            "note": item["note"],
        })
    return ranked
