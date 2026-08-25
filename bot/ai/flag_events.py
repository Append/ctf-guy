#!/usr/bin/env python3
"""Flag confirmation event registry.

Solvers register an asyncio.Event keyed by challenge_id before starting.
The bot's /submit HTTP handler calls notify() when a correct flag is
confirmed. The solver's timeout logic awaits the event to switch from
the hard deadline to a grace period for writing deliverables.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Grace period after flag confirmed — solver gets this long to write deliverables
FLAG_GRACE_PERIOD = 120  # seconds


@dataclass(frozen=True)
class FlagResult:
    """Metadata about a confirmed flag, stored alongside the event."""

    flag: str
    solver_id: str = ""
    model: str = ""


# challenge_id -> asyncio.Event
_registry: dict[int, asyncio.Event] = {}

# challenge_id -> FlagResult (populated on first notify)
_results: dict[int, FlagResult] = {}


def register(challenge_id: int) -> asyncio.Event:
    """Register a flag event for a challenge. Returns the Event to await."""
    if challenge_id not in _registry:
        _registry[challenge_id] = asyncio.Event()
        log.debug(f"Flag event registered for challenge {challenge_id}")
    return _registry[challenge_id]


def notify(
    challenge_id: int,
    *,
    flag: str = "",
    solver_id: str = "",
    model: str = "",
) -> None:
    """Signal that a correct flag was confirmed for this challenge.

    First call for a given challenge_id wins — subsequent calls are ignored
    (the event is already set and the result already stored).
    """
    event = _registry.get(challenge_id)
    if not event:
        log.debug(f"Flag notify for challenge {challenge_id} — no registered listener")
        return

    if event.is_set():
        # First notify already won; ignore this call.
        log.debug(f"Flag notify for challenge {challenge_id} — already set, ignoring")
        return

    _results[challenge_id] = FlagResult(flag=flag, solver_id=solver_id, model=model)
    log.info(f"Flag confirmed for challenge {challenge_id} " f"(solver={solver_id!r}, model={model!r}) — setting event")
    event.set()


def get_result(challenge_id: int) -> FlagResult | None:
    """Return the stored FlagResult for a challenge, or None if not yet set."""
    return _results.get(challenge_id)


def unregister(challenge_id: int) -> None:
    """Remove the event and result for a challenge (call in finally block)."""
    _registry.pop(challenge_id, None)
    _results.pop(challenge_id, None)
