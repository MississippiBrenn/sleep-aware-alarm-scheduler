"""Tiny SQLite store: settings, tokens, one row per night, and an event log."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import asdict, fields
from datetime import datetime, timezone

from .config import settings
from .planner import Prefs


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    c = sqlite3.connect(settings.db_path)
    c.row_factory = sqlite3.Row
    return c


def init() -> None:
    with closing(_conn()) as c, c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS nights (
                wake_date TEXT PRIMARY KEY, wake_at TEXT, confidence TEXT, reason TEXT,
                window_start TEXT, window_end TEXT, updated_at TEXT,
                fired_at TEXT, fire_ok INTEGER
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, level TEXT, msg TEXT
            );
            """
        )


def get_json(key: str, default=None):
    with closing(_conn()) as c:
        row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_json(key: str, value) -> None:
    with closing(_conn()) as c, c:
        c.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )


def get_prefs() -> Prefs:
    stored = get_json("prefs", {}) or {}
    allowed = {f.name for f in fields(Prefs)}
    return Prefs(**{k: v for k, v in stored.items() if k in allowed})


def set_prefs(p: Prefs) -> None:
    set_json("prefs", asdict(p))


def log(level: str, msg: str) -> None:
    print(f"[{level}] {msg}", flush=True)
    with closing(_conn()) as c, c:
        c.execute(
            "INSERT INTO events(ts,level,msg) VALUES(?,?,?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), level, msg),
        )
        c.execute("DELETE FROM events WHERE id <= (SELECT MAX(id) FROM events) - 200")


def recent_events(n: int = 15) -> list[dict]:
    with closing(_conn()) as c:
        rows = c.execute("SELECT ts,level,msg FROM events ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    return [dict(r) for r in rows]


def save_night(wake_date: str, wake_at: str, confidence: str, reason: str, w0: str, w1: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(_conn()) as c, c:
        c.execute(
            """INSERT INTO nights(wake_date,wake_at,confidence,reason,window_start,window_end,updated_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(wake_date) DO UPDATE SET wake_at=excluded.wake_at,
                 confidence=excluded.confidence, reason=excluded.reason,
                 window_start=excluded.window_start, window_end=excluded.window_end,
                 updated_at=excluded.updated_at""",
            (wake_date, wake_at, confidence, reason, w0, w1, now),
        )


def get_night(wake_date: str) -> dict | None:
    with closing(_conn()) as c:
        row = c.execute("SELECT * FROM nights WHERE wake_date=?", (wake_date,)).fetchone()
    return dict(row) if row else None


def latest_night() -> dict | None:
    with closing(_conn()) as c:
        row = c.execute("SELECT * FROM nights ORDER BY wake_date DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def mark_fired(wake_date: str, ok: bool) -> None:
    with closing(_conn()) as c, c:
        c.execute(
            "UPDATE nights SET fired_at=?, fire_ok=? WHERE wake_date=?",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), int(ok), wake_date),
        )
