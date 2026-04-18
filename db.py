"""
SQLite database layer for CompBot.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "compbot.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS competitions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT NOT NULL UNIQUE,
                name        TEXT,
                closing_date TEXT,
                status      TEXT NOT NULL DEFAULT 'pending',
                scam_score  INTEGER DEFAULT 0,
                scam_flags  TEXT,
                warnings    TEXT,
                requirements TEXT,
                fields      TEXT,
                added_at    TEXT NOT NULL,
                filled_at   TEXT,
                notes       TEXT
            )
        """)
        # migrate existing DBs that lack the new columns
        for col, definition in [("scam_score", "INTEGER DEFAULT 0"), ("scam_flags", "TEXT")]:
            try:
                conn.execute(f"ALTER TABLE competitions ADD COLUMN {col} {definition}")
            except Exception:
                pass


def add_competition(url: str, extraction: dict, scam_score: int = 0, scam_flags: list | None = None) -> int:
    """Insert a new competition from Claude's extraction output. Returns new id."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO competitions (url, name, closing_date, status, scam_score, scam_flags, warnings, requirements, fields, added_at)
            VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
            """,
            (
                url,
                extraction.get("competition_name"),
                extraction.get("closing_date"),
                scam_score,
                json.dumps(scam_flags or []),
                json.dumps(extraction.get("warnings", [])),
                json.dumps(extraction.get("requirements", [])),
                json.dumps(extraction.get("fields", [])),
                now,
            ),
        )
        return cur.lastrowid


def get_competition(comp_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM competitions WHERE id = ?", (comp_id,)).fetchone()


def list_competitions(status: str | None = None) -> list[sqlite3.Row]:
    with _connect() as conn:
        if status:
            return conn.execute(
                "SELECT * FROM competitions WHERE status = ? ORDER BY id DESC", (status,)
            ).fetchall()
        return conn.execute("SELECT * FROM competitions ORDER BY id DESC").fetchall()


def update_status(comp_id: int, status: str, filled_at: str | None = None):
    with _connect() as conn:
        if filled_at:
            conn.execute(
                "UPDATE competitions SET status = ?, filled_at = ? WHERE id = ?",
                (status, filled_at, comp_id),
            )
        else:
            conn.execute("UPDATE competitions SET status = ? WHERE id = ?", (status, comp_id))


def url_exists(url: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM competitions WHERE url = ?", (url,)).fetchone()
        return row is not None
