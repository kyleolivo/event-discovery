# event-discovery

Finds local SF events so you don't have to. Syncs event calendars from venue websites into a local database, then uses Claude to surface events that match your interests.

The primary interface is an **MCP server** that integrates directly with Claude Desktop or Claude.ai — just ask Claude "what's going on in SF this weekend?" and it queries your local event database in real time.

## Setup

```bash
uv venv --python 3.13
source .venv/bin/activate
uv pip install -e .

# Add your Anthropic API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

## Integrating with Claude Desktop

Add the MCP server to your Claude Desktop config at
`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "sf-events": {
      "command": "/path/to/event-discovery/.venv/bin/events",
      "args": ["mcp-server"]
    }
  }
}
```

Once configured, Claude can call these tools directly during a conversation:

| Tool | What it does |
|------|-------------|
| `list_events` | Ranked upcoming events matched to your preferences |
| `search_events` | Keyword search across title, description, venue |
| `sync_events` | Pull the latest events from all sources |
| `get_preferences` | Show your current interest profile |
| `set_preferences` | Update your interest profile |

Example prompts:
- *"What SF events should I go to this week?"*
- *"Any free outdoor concerts coming up?"*
- *"Search for jazz events in the next month"*
- *"Sync my events and show me what's new"*

## CLI usage

```bash
# Pull latest events from all sources
events sync

# Show upcoming events ranked by your interests
events list

# Show next 14 days, hide events scored below 7
events list --days 14 --min-score 7

# Search for events by keyword
events list --search "jazz" --days 60

# Print a formatted weekly digest
events digest

# Show all upcoming events without LLM ranking
events list --no-rank --days 90

# Set or update your interest preferences
events prefs

# View current preferences
events prefs --show

# List sources that have been synced
events sources

# Start the MCP server (used by Claude Desktop)
events mcp-server
```

## How it works

1. **Sync** — collectors pull from venue sources and upsert events into SQLite at
   `~/.event-discovery/events.db`. Repeated syncs are safe and idempotent.

2. **Store** — SQLite with events, sources, and preferences tables. All timestamps in UTC.

3. **Rank** — Claude (claude-haiku-4-5 by default) scores each event 1–10 against your
   interest profile and writes a one-line note explaining the match. Override the model with
   `ANTHROPIC_MODEL=claude-sonnet-4-6` in your `.env`.

## Supported source types

### `tribe_events` — WordPress Events Calendar plugin
Many SF venues run this plugin, which exposes a JSON API at
`/wp-json/tribe/events/v1/events`.

Currently configured:
- Yerba Buena Gardens Festival
- SF Civic Center

### `ticketmaster` — Ticketmaster Discovery API
Covers all major venues selling through Ticketmaster/Live Nation — The Fillmore,
Warfield, Chase Center, Davies Symphony Hall, SFJAZZ, Bill Graham Civic, etc.
Requires a free API key from https://developer.ticketmaster.com/

### `luma` — Luma (lu.ma) calendars
Uses the Luma public API. Set `url` to the Luma calendar page; the slug is used
as the `calendar_id`. You can override with an explicit `"calendar_id"` key in
the source config.

Currently configured:
- Luma SF (`lu.ma/sf`)

### `ical` — Standard iCalendar (.ics) feeds
Any venue that publishes a `.ics` feed. Many ticketing platforms (Eventbrite,
AudienceView, Tessitura) can export one.

## Adding more sources

Edit `DEFAULT_SOURCES` in [`src/event_discovery/cli.py`](src/event_discovery/cli.py):

```python
DEFAULT_SOURCES = [
    # Tribe Events (WordPress)
    {"name": "Your Venue", "url": "https://yourvenue.org", "kind": "tribe_events"},

    # iCal feed
    {"name": "Your Venue", "url": "https://yourvenue.org/events.ics", "kind": "ical"},

    # Luma calendar
    {"name": "Your Community", "url": "https://lu.ma/your-slug", "kind": "luma"},
    # or with explicit calendar_id:
    {"name": "Your Community", "url": "https://lu.ma/...", "kind": "luma", "calendar_id": "cal-xxx"},
]
```

## Scheduled digests

Use cron (or any scheduler) to get a weekly briefing:

```cron
# Every Monday at 8am — sync and print digest
0 8 * * 1 /path/to/.venv/bin/events sync && /path/to/.venv/bin/events digest
```

Or pipe the output to an email or messaging webhook.
