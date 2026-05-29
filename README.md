# event-discovery

Finds local SF events so you don't have to. Syncs event calendars from venue websites into a local database, then uses OpenAI to surface events that match your interests.

## Setup

```bash
uv venv --python 3.13
uv pip install -e .

# Add your OpenAI API key
echo "OPENAI_API_KEY=sk-..." > .env
```

## Usage

```bash
# Pull latest events from all sources
events sync

# Show upcoming events ranked by your interests
events list

# Show next 14 days, hide events scored below 7
events list --days 14 --min-score 7

# Show all upcoming events without LLM ranking
events list --no-rank --days 90

# Set or update your interest preferences
events prefs

# View current preferences
events prefs --show

# List sources that have been synced
events sources
```

## How it works

1. **Sync** — collectors pull from venue APIs (currently supports sites running The Events Calendar WordPress plugin). Events are upserted by `(source, external_id)` so repeated syncs are safe and idempotent.

2. **Store** — SQLite at `~/.event-discovery/events.db`. All timestamps stored as UTC.

3. **Rank** — `events list` sends your upcoming events and preference profile to OpenAI, which returns them scored 1–10 with a one-line note explaining the match.

## Adding more sources

Sites running The Events Calendar plugin expose a JSON API at `/wp-json/tribe/events/v1/events`. To add one, edit `DEFAULT_SOURCES` in [src/event_discovery/cli.py](src/event_discovery/cli.py):

```python
DEFAULT_SOURCES = [
    {"name": "Yerba Buena Gardens Festival", "url": "https://ybgfestival.org", "kind": "tribe_events"},
    {"name": "Your Venue",                   "url": "https://yourvenue.org",   "kind": "tribe_events"},
]
```
