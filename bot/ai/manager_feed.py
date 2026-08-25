#!/usr/bin/env python3
"""ManagerFeed — shared event buffer between solver streams and the manager.

A simple deque that stream processors push events into.
The manager reads from it to detect anti-patterns.
"""

import time
from collections import deque


class ManagerFeed:
    """Thread-safe-ish ring buffer for solver events.

    Not truly thread-safe, but all access is from the same asyncio
    event loop so concurrent coroutines won't corrupt it.
    """

    def __init__(self, maxlen: int = 200):
        self.events: deque[dict] = deque(maxlen=maxlen)

    def push(self, event_type: str, **data) -> None:
        """Push an event into the feed."""
        self.events.append({"type": event_type, "ts": time.time(), **data})

    def recent(self, n: int = 50) -> list[dict]:
        """Get the last N events."""
        return list(self.events)[-n:]

    def tool_calls(self) -> list[dict]:
        """Get all tool_call events in the buffer."""
        return [e for e in self.events if e["type"] == "tool_call"]

    def texts(self) -> list[dict]:
        """Get all text events in the buffer."""
        return [e for e in self.events if e["type"] == "text"]

    def last_event_time(self) -> float:
        """Timestamp of the most recent event, or 0."""
        if self.events:
            return self.events[-1].get("ts", 0)
        return 0

    def __len__(self) -> int:
        return len(self.events)
