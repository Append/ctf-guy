#!/usr/bin/env python3
"""CTF instance CRUD operations."""

import sqlite3
from dataclasses import dataclass


@dataclass
class CTFRecord:
    id: int
    name: str
    url: str
    guild_id: str
    category_id: str | None
    channel_id: str
    flag_format: str
    active: bool


def upsert_ctf(
    conn: sqlite3.Connection,
    name: str,
    url: str,
    guild_id: str,
    channel_id: str,
    category_id: str | None = None,
    flag_format: str = r"kernel\{.*\}",
) -> int:
    """Create or update a CTF record. Returns the CTF id."""
    # Deactivate previous CTFs for this guild
    conn.execute(
        "UPDATE ctfs SET active = 0 WHERE guild_id = ? AND active = 1",
        (guild_id,),
    )
    cursor = conn.execute(
        """INSERT INTO ctfs (name, url, guild_id, category_id, channel_id, flag_format)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, url, guild_id, category_id, channel_id, flag_format),
    )
    conn.commit()
    return cursor.lastrowid


def get_active_ctf(conn: sqlite3.Connection, guild_id: str) -> CTFRecord | None:
    """Get the active CTF for a guild."""
    row = conn.execute(
        "SELECT * FROM ctfs WHERE guild_id = ? AND active = 1 ORDER BY id DESC LIMIT 1",
        (guild_id,),
    ).fetchone()
    if not row:
        return None
    return CTFRecord(
        id=row["id"],
        name=row["name"],
        url=row["url"],
        guild_id=row["guild_id"],
        category_id=row["category_id"],
        channel_id=row["channel_id"],
        flag_format=row["flag_format"],
        active=bool(row["active"]),
    )
