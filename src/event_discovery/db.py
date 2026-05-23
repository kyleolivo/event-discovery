"""SQLite database layer. All times stored as UTC ISO-8601 strings."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

DB_PATH = Path.home() / ".event-discovery" / "events.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,  -- 'tribe_events' | 'ical'
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES sources(id),
    external_id     TEXT NOT NULL,          -- id from the source system
    title           TEXT NOT NULL,
    description     TEXT,
    start_utc       TEXT NOT NULL,          -- ISO-8601 UTC
    end_utc         TEXT,
    timezone        TEXT,
    venue_name      TEXT,
    venue_address   TEXT,
    url             TEXT,
    cost            TEXT,
    image_url       TEXT,
    raw_json        TEXT,                   -- full source payload
    first_seen_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source_id, external_id)
);

CREATE TABLE IF NOT EXISTS preferences (
    id          INTEGER PRIMARY KEY,
    body        TEXT NOT NULL,              -- free-form text describing interests
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_source(conn: sqlite3.Connection, name: str, url: str, kind: str) -> int:
    conn.execute(
        """INSERT INTO sources (name, url, kind)
           VALUES (?, ?, ?)
           ON CONFLICT(url) DO UPDATE SET name=excluded.name, kind=excluded.kind""",
        (name, url, kind),
    )
    row = conn.execute("SELECT id FROM sources WHERE url = ?", (url,)).fetchone()
    return row["id"]


def upsert_event(conn: sqlite3.Connection, source_id: int, event: dict) -> None:
    conn.execute(
        """INSERT INTO events
               (source_id, external_id, title, description, start_utc, end_utc,
                timezone, venue_name, venue_address, url, cost, image_url, raw_json)
           VALUES
               (:source_id, :external_id, :title, :description, :start_utc, :end_utc,
                :timezone, :venue_name, :venue_address, :url, :cost, :image_url, :raw_json)
           ON CONFLICT(source_id, external_id) DO UPDATE SET
               title         = excluded.title,
               description   = excluded.description,
               start_utc     = excluded.start_utc,
               end_utc       = excluded.end_utc,
               timezone      = excluded.timezone,
               venue_name    = excluded.venue_name,
               venue_address = excluded.venue_address,
               url           = excluded.url,
               cost          = excluded.cost,
               image_url     = excluded.image_url,
               raw_json      = excluded.raw_json,
               last_seen_at  = datetime('now')""",
        {"source_id": source_id, **event},
    )


def get_upcoming_events(conn: sqlite3.Connection, days: int = 60) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT e.*, s.name as source_name
           FROM events e
           JOIN sources s ON s.id = e.source_id
           WHERE date(e.start_utc) BETWEEN date('now') AND date('now', ? || ' days')
           ORDER BY e.start_utc""",
        (f"+{days}",),
    ).fetchall()


def get_preferences(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT body FROM preferences ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    return row["body"] if row else None


def set_preferences(conn: sqlite3.Connection, body: str) -> None:
    conn.execute(
        """INSERT INTO preferences (body) VALUES (?)""",
        (body,),
    )
