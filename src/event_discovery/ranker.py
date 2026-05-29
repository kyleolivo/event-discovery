"""LLM-based event ranking using the OpenAI API."""

import json
import os
import sqlite3

from openai import OpenAI

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def _event_summary(row: sqlite3.Row) -> dict:
    return {
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


MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")

RANKING_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "date": {"type": "string"},
                    "score": {"type": "integer"},
                    "note": {"type": "string"},
                    "url": {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "null"},
                        ],
                    },
                },
                "required": ["title", "date", "score", "note", "url"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["events"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are an SF event concierge. Given a list of upcoming events and the user's
interest profile, rank the events from most to least relevant. For each event, assign a relevance
score from 1–10 and write a one-sentence note explaining why it matches (or doesn't match) the
user's interests. Be concise and direct. Skip events with a score below 4 unless the list is short.

Return only events in the requested JSON schema.
"""


def rank_events(
    events: list[sqlite3.Row],
    preferences: str,
    days: int = 60,
) -> list[dict]:
    if not events:
        return []

    summaries = [_event_summary(e) for e in events]
    user_message = f"""User interests:
{preferences}

Upcoming events (next {days} days):
{json.dumps(summaries, indent=2)}
"""

    client = _get_client()
    response = client.responses.create(
        model=MODEL,
        instructions=SYSTEM_PROMPT,
        input=user_message,
        max_output_tokens=4096,
        text={
            "format": {
                "type": "json_schema",
                "name": "ranked_events",
                "schema": RANKING_SCHEMA,
                "strict": True,
            },
        },
    )

    payload = json.loads(response.output_text)
    return payload["events"]
