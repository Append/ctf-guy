#!/usr/bin/env python3
"""Per-thread conversation history CRUD."""

import json
import sqlite3
from dataclasses import dataclass


@dataclass
class ConversationMessage:
    role: str  # 'user', 'assistant', 'tool'
    content: str
    tool_calls: list[dict] | None = None
    tool_results: list[dict] | None = None
    model: str | None = None


def add_message(
    conn: sqlite3.Connection,
    thread_id: str,
    role: str,
    content: str,
    tool_calls: list[dict] | None = None,
    tool_results: list[dict] | None = None,
    model: str | None = None,
) -> None:
    """Add a message to the conversation history."""
    token_estimate = len(content) // 4  # Rough estimate
    conn.execute(
        """INSERT INTO conversations (thread_id, role, content, tool_calls, tool_results, model, token_estimate)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            thread_id,
            role,
            content,
            json.dumps(tool_calls) if tool_calls else None,
            json.dumps(tool_results) if tool_results else None,
            model,
            token_estimate,
        ),
    )
    conn.commit()


def get_history(
    conn: sqlite3.Connection,
    thread_id: str,
    max_tokens: int = 100_000,
) -> list[ConversationMessage]:
    """Get conversation history for a thread, trimmed to fit token budget.

    Keeps the most recent messages, trimming from the front.
    """
    rows = conn.execute(
        """SELECT role, content, tool_calls, tool_results, model, token_estimate
           FROM conversations
           WHERE thread_id = ?
           ORDER BY created_at ASC""",
        (thread_id,),
    ).fetchall()

    messages = []
    for row in rows:
        messages.append(
            ConversationMessage(
                role=row["role"],
                content=row["content"],
                tool_calls=json.loads(row["tool_calls"]) if row["tool_calls"] else None,
                tool_results=(
                    json.loads(row["tool_results"]) if row["tool_results"] else None
                ),
                model=row["model"],
            )
        )

    # Trim from front if over budget
    total_tokens = sum(len(m.content) // 4 for m in messages)
    while total_tokens > max_tokens and len(messages) > 2:
        removed = messages.pop(0)
        total_tokens -= len(removed.content) // 4

    return messages


def clear_history(conn: sqlite3.Connection, thread_id: str) -> None:
    """Clear all conversation history for a thread."""
    conn.execute("DELETE FROM conversations WHERE thread_id = ?", (thread_id,))
    conn.commit()
