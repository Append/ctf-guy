#!/usr/bin/env python3
"""Challenge metadata CRUD operations."""

import sqlite3
from dataclasses import dataclass


@dataclass
class ChallengeRecord:
    id: int
    ctf_id: int
    ctfd_id: int
    name: str
    slug: str
    category: str
    points: int
    description: str | None
    thread_id: str | None
    challenge_dir: str | None
    solved: bool
    flag: str | None


def upsert_challenge(
    conn: sqlite3.Connection,
    ctf_id: int,
    ctfd_id: int,
    name: str,
    slug: str,
    category: str,
    points: int,
    description: str | None = None,
    thread_id: str | None = None,
    challenge_dir: str | None = None,
) -> int:
    """Create or update a challenge record. Returns the challenge id."""
    cursor = conn.execute(
        """INSERT INTO challenges (ctf_id, ctfd_id, name, slug, category, points, description, thread_id, challenge_dir)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(ctf_id, ctfd_id) DO UPDATE SET
               name=excluded.name, slug=excluded.slug, category=excluded.category,
               points=excluded.points, description=excluded.description,
               thread_id=COALESCE(excluded.thread_id, challenges.thread_id),
               challenge_dir=COALESCE(excluded.challenge_dir, challenges.challenge_dir)""",
        (
            ctf_id,
            ctfd_id,
            name,
            slug,
            category,
            points,
            description,
            thread_id,
            challenge_dir,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def get_by_thread(conn: sqlite3.Connection, thread_id: str) -> ChallengeRecord | None:
    """Look up a challenge by its Discord thread ID."""
    row = conn.execute(
        "SELECT * FROM challenges WHERE thread_id = ?", (thread_id,)
    ).fetchone()
    if not row:
        return None
    return _row_to_record(row)


def get_existing_ctfd_ids(conn: sqlite3.Connection, ctf_id: int) -> set[int]:
    """Get all ctfd_ids already tracked for a CTF."""
    rows = conn.execute(
        "SELECT ctfd_id FROM challenges WHERE ctf_id = ?", (ctf_id,)
    ).fetchall()
    return {row["ctfd_id"] for row in rows}


def mark_solved(conn: sqlite3.Connection, challenge_id: int, flag: str) -> None:
    """Mark a challenge as solved."""
    conn.execute(
        "UPDATE challenges SET solved = 1, flag = ?, solved_at = datetime('now') WHERE id = ?",
        (flag, challenge_id),
    )
    conn.commit()


def get_all(conn: sqlite3.Connection, ctf_id: int) -> list[ChallengeRecord]:
    """Get all challenges for a CTF."""
    rows = conn.execute(
        "SELECT * FROM challenges WHERE ctf_id = ? ORDER BY points, category",
        (ctf_id,),
    ).fetchall()
    return [_row_to_record(r) for r in rows]


def get_unsolved(conn: sqlite3.Connection, ctf_id: int) -> list[ChallengeRecord]:
    """Get unsolved challenges for a CTF."""
    rows = conn.execute(
        "SELECT * FROM challenges WHERE ctf_id = ? AND solved = 0 ORDER BY points, category",
        (ctf_id,),
    ).fetchall()
    return [_row_to_record(r) for r in rows]


def _row_to_record(row: sqlite3.Row) -> ChallengeRecord:
    return ChallengeRecord(
        id=row["id"],
        ctf_id=row["ctf_id"],
        ctfd_id=row["ctfd_id"],
        name=row["name"],
        slug=row["slug"],
        category=row["category"],
        points=row["points"],
        description=row["description"],
        thread_id=row["thread_id"],
        challenge_dir=row["challenge_dir"],
        solved=bool(row["solved"]),
        flag=row["flag"],
    )
