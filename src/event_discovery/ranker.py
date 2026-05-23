"""LLM-based event ranking using the Claude API."""

import json
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


SYSTEM_PROMPT = """You are an SF event concierge. Given a list of upcoming events and the user's
interest profile, rank the events from most to least relevant. For each event, assign a relevance
score from 1–10 and write a one-sentence note explaining why it matches (or doesn't match) the
user's interests. Be concise and direct. Skip events with a score below 4 unless the list is short.

Respond with a JSON array (no markdown fences), each element:
{
  "title": "<event title>",
  "date": "<YYYY-MM-DD>",
  "score": <1-10>,
  "note": "<one sentence>",
  "url": "<url or null>"
}
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
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    text = message.content[0].text.strip()
    return json.loads(text)
