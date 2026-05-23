"""CLI entry point. Usage: events <command> [options]"""

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import click


def _load_dotenv():
    """Load .env from the project root (directory containing this package)."""
    env_path = Path(__file__).parent.parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()
from rich.console import Console
from rich.table import Table
from rich import box

from event_discovery import db, ranker
from event_discovery.collectors import tribe_events

console = Console()

# Display times in SF local time (PDT = UTC-7, PST = UTC-8).
# Using a fixed offset is fine for display purposes; the raw UTC is stored in the DB.
_PDT = timezone(timedelta(hours=-7))


def _fmt_time(utc_str: str | None) -> str:
    if not utc_str:
        return ""
    try:
        dt = datetime.fromisoformat(utc_str.replace(" ", "T")).replace(tzinfo=timezone.utc)
        local = dt.astimezone(_PDT)
        return local.strftime("%-I:%M %p")
    except ValueError:
        return utc_str[11:16]

# ---------------------------------------------------------------------------
# Built-in sources. More can be added via `events sources add`.
# ---------------------------------------------------------------------------
DEFAULT_SOURCES = [
    {
        "name": "Yerba Buena Gardens Festival",
        "url": "https://ybgfestival.org",
        "kind": "tribe_events",
    },
]


@click.group()
def cli():
    """SF Event Discovery — sync, rank, and browse local events."""


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------
@cli.command()
@click.option("--source", "source_filter", default=None, help="Sync only this source name")
def sync(source_filter: str | None):
    """Pull fresh events from all configured sources."""
    sources = DEFAULT_SOURCES
    if source_filter:
        sources = [s for s in sources if source_filter.lower() in s["name"].lower()]
        if not sources:
            console.print(f"[red]No source matching '{source_filter}'[/red]")
            raise SystemExit(1)

    with db.get_conn() as conn:
        for source in sources:
            console.print(f"Syncing [bold]{source['name']}[/bold]...")
            if source["kind"] == "tribe_events":
                added, updated = tribe_events.sync(conn, source["name"], source["url"])
                console.print(
                    f"  [green]+{added} new[/green]  [dim]{updated} updated[/dim]"
                )
            else:
                console.print(f"  [yellow]Unknown kind '{source['kind']}', skipping[/yellow]")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------
@cli.command("list")
@click.option("--days", default=30, show_default=True, help="Look-ahead window in days")
@click.option("--rank/--no-rank", default=True, show_default=True,
              help="Use LLM to rank by your preferences")
@click.option("--min-score", default=5, show_default=True,
              help="Hide events with score below this (only with --rank)")
def list_events(days: int, rank: bool, min_score: int):
    """Show upcoming events, optionally ranked by your interests."""
    with db.get_conn() as conn:
        events = db.get_upcoming_events(conn, days=days)

        if not events:
            console.print("[yellow]No events found. Run 'events sync' first.[/yellow]")
            return

        if rank:
            prefs = db.get_preferences(conn)
            if not prefs:
                console.print(
                    "[yellow]No preferences set — showing all events unranked.[/yellow]\n"
                    "Run [bold]events prefs[/bold] to set your interests."
                )
                rank = False

        if rank:
            console.print(f"Ranking {len(events)} events against your preferences…")
            ranked = ranker.rank_events(events, prefs, days=days)
            ranked = [e for e in ranked if e["score"] >= min_score]
            _print_ranked(ranked)
        else:
            _print_plain(events)


def _print_ranked(ranked: list[dict]):
    if not ranked:
        console.print("[dim]No events matched your interests at the current score threshold.[/dim]")
        return

    table = Table(box=box.SIMPLE_HEAD, show_lines=False, expand=True)
    table.add_column("Score", style="bold", width=6, justify="center")
    table.add_column("Date", width=12)
    table.add_column("Event", min_width=24)
    table.add_column("Note", min_width=30)

    for e in ranked:
        score = e["score"]
        color = "green" if score >= 8 else "yellow" if score >= 6 else "dim"
        table.add_row(
            f"[{color}]{score}/10[/{color}]",
            e.get("date", ""),
            f"[link={e.get('url', '')}]{e['title']}[/link]" if e.get("url") else e["title"],
            f"[dim]{e.get('note', '')}[/dim]",
        )

    console.print(table)


def _print_plain(events):
    table = Table(box=box.SIMPLE_HEAD, show_lines=False, expand=True)
    table.add_column("Date", width=12)
    table.add_column("Time", width=10)
    table.add_column("Event", min_width=28)
    table.add_column("Venue", min_width=20)
    table.add_column("Source", width=16)

    for e in events:
        start = e["start_utc"] or ""
        table.add_row(
            start[:10],
            _fmt_time(e["start_utc"]),
            f"[link={e['url']}]{e['title']}[/link]" if e["url"] else e["title"],
            e["venue_name"] or "",
            e["source_name"],
        )

    console.print(table)


# ---------------------------------------------------------------------------
# prefs
# ---------------------------------------------------------------------------
@cli.command()
@click.option("--show", is_flag=True, help="Print current preferences and exit")
def prefs(show: bool):
    """View or update your event interest preferences."""
    with db.get_conn() as conn:
        current = db.get_preferences(conn)

        if show:
            if current:
                console.print(current)
            else:
                console.print("[dim]No preferences set.[/dim]")
            return

        console.print(
            "Enter your event interests (music genres, art forms, activities, etc.).\n"
            "Type your preferences below, then press [bold]Ctrl-D[/bold] when done.\n"
            "[dim]Current:[/dim]"
        )
        if current:
            console.print(f"[dim]{current}[/dim]\n")

        lines = []
        try:
            while True:
                lines.append(input())
        except EOFError:
            pass

        body = "\n".join(lines).strip()
        if body:
            db.set_preferences(conn, body)
            console.print("[green]Preferences saved.[/green]")
        else:
            console.print("[yellow]No input — preferences unchanged.[/yellow]")


# ---------------------------------------------------------------------------
# sources (list only for now)
# ---------------------------------------------------------------------------
@cli.command()
def sources():
    """List configured event sources."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM sources ORDER BY name").fetchall()

    if not rows:
        console.print("[dim]No sources synced yet. Run 'events sync' first.[/dim]")
        return

    table = Table(box=box.SIMPLE_HEAD)
    table.add_column("ID", width=4)
    table.add_column("Name")
    table.add_column("Kind", width=14)
    table.add_column("URL")
    table.add_column("Added", width=12)

    for r in rows:
        table.add_row(str(r["id"]), r["name"], r["kind"], r["url"], r["created_at"][:10])

    console.print(table)
