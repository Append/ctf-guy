#!/usr/bin/env python3
"""SQLite schema initialization."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS ctfs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    category_id TEXT,
    channel_id TEXT NOT NULL,
    flag_format TEXT DEFAULT 'kernel\\{.*\\}',
    created_at TEXT DEFAULT (datetime('now')),
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS challenges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ctf_id INTEGER NOT NULL REFERENCES ctfs(id),
    ctfd_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    category TEXT NOT NULL,
    points INTEGER NOT NULL,
    description TEXT,
    thread_id TEXT,
    challenge_dir TEXT,
    solved INTEGER DEFAULT 0,
    flag TEXT,
    solved_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(ctf_id, ctfd_id)
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'tool')),
    content TEXT NOT NULL,
    tool_calls TEXT,
    tool_results TEXT,
    model TEXT,
    token_estimate INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_conversations_thread ON conversations(thread_id, created_at);
CREATE INDEX IF NOT EXISTS idx_challenges_thread ON challenges(thread_id);
CREATE INDEX IF NOT EXISTS idx_challenges_ctf ON challenges(ctf_id);
"""


def init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize the database and return a connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
