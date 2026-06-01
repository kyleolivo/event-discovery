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


MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

SYSTEM_PROMPT = """You are an SF event concierge. Given a list of upcoming events and the user's
interest profile, rank the events from most to least relevant. For each event, assign a relevance
score from 1–10 and write a one-sentence note explaining why it matches (or doesn't match) the
user's interests. Be concise and direct. Skip events with a score below 4 unless the list is short.

Return ONLY a JSON object with this exact structure:
{
  "events": [
    {"title": "...", "date": "YYYY-MM-DD", "score": 8, "note": "...", "url": "..."},
    ...
  ]
}"""


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
{json.dumps(summaries, indent=2)}"""

    client = _get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    text = response.content[0].text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    payload = json.loads(text)
    return payload["events"]
